# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from neutron_lib import constants as lib_const
from neutron_lib import exceptions as lib_exc
from oslo_config import cfg
from oslo_log import log as logging

from neutron._i18n import _
from neutron.callbacks import events
from neutron.callbacks import registry
from neutron.callbacks import resources
from neutron.db import servicetype_db as st_db
from neutron import manager
from neutron.plugins.common import constants
from neutron.services import provider_configuration
from neutron.services import service_base

LOG = logging.getLogger(__name__)


class DriverController(object):
    """Driver controller for the L3 service plugin.

    This component is responsible for dispatching router requests to L3
    service providers and for performing the bookkeeping about which
    driver is associated with a given router.

    This is not intended to be accessed by the drivers or the l3 plugin.
    All of the methods are marked as private to reflect this.
    """

    def __init__(self, l3_plugin):
        self.l3_plugin = l3_plugin
        self._stm = st_db.ServiceTypeManager.get_instance()
        self._stm.add_provider_configuration(
                constants.L3_ROUTER_NAT, _LegacyPlusProviderConfiguration())
        self._load_drivers()
        registry.subscribe(self._set_router_provider,
                           resources.ROUTER, events.PRECOMMIT_CREATE)
        registry.subscribe(self._update_router_provider,
                           resources.ROUTER, events.PRECOMMIT_UPDATE)
        registry.subscribe(self._clear_router_provider,
                           resources.ROUTER, events.PRECOMMIT_DELETE)

    def _load_drivers(self):
        self.drivers, self.default_provider = (
            service_base.load_drivers(constants.L3_ROUTER_NAT, self.l3_plugin))
        # store the provider name on each driver to make finding inverse easy
        for provider_name, driver in self.drivers.items():
            setattr(driver, 'name', provider_name)

    @property
    def _flavor_plugin(self):
        if not hasattr(self, '_flavor_plugin_ref'):
            _service_plugins = manager.NeutronManager.get_service_plugins()
            self._flavor_plugin_ref = _service_plugins[constants.FLAVORS]
        return self._flavor_plugin_ref

    def _set_router_provider(self, resource, event, trigger, context, router,
                             router_db, **kwargs):
        """Associates a router with a service provider.

        Association is done by flavor_id if it's specified, otherwise it will
        fallback to determining which loaded driver supports the ha/distributed
        attributes associated with the router.
        """
        if _flavor_specified(router):
            router_db.flavor_id = router['flavor_id']
        drv = self._get_provider_for_create(context, router)
        _ensure_driver_supports_request(drv, router)
        self._stm.add_resource_association(context, 'L3_ROUTER_NAT',
                                           drv.name, router['id'])

    def _clear_router_provider(self, resource, event, trigger, context,
                               router_id, **kwargs):
        """Remove the association between a router and a service provider."""
        self._stm.del_resource_associations(context, [router_id])

    def _update_router_provider(self, resource, event, trigger, context,
                                router_id, router, old_router, router_db,
                                **kwargs):
        """Handle transition between providers.

        The provider can currently be changed only by the caller updating
        'ha' and/or 'distributed' attributes. If we allow updates of flavor_id
        directly in the future those requests will also land here.
        """
        drv = self._get_provider_for_router(context, router_id)
        new_drv = None
        if _flavor_specified(router):
            if router['flavor_id'] != old_router['flavor_id']:
                # TODO(kevinbenton): this is currently disallowed by the API
                # so we shouldn't hit it but this is a placeholder to add
                # support later.
                raise NotImplementedError()

        # the following is to support updating the 'ha' and 'distributed'
        # attributes via the API.
        try:
            _ensure_driver_supports_request(drv, router)
        except lib_exc.Invalid:
            # the current driver does not support this request, we need to
            # migrate to a new provider. populate the distributed and ha
            # flags from the previous state if not in the update so we can
            # determine the target provider appropriately.
            # NOTE(kevinbenton): if the router is associated with a flavor
            # we bail because changing the provider without changing
            # the flavor will make things inconsistent. We can probably
            # update the flavor automatically in the future.
            if old_router['flavor_id']:
                raise lib_exc.Invalid(_(
                    "Changing the 'ha' and 'distributed' attributes on a "
                    "router associated with a flavor is not supported."))
            if 'distributed' not in router:
                router['distributed'] = old_router['distributed']
            if 'ha' not in router:
                router['ha'] = old_router['distributed']
            new_drv = self._attrs_to_driver(router)
        if new_drv:
            LOG.debug("Router %(id)s migrating from %(old)s provider to "
                      "%(new)s provider.", {'id': router_id, 'old': drv,
                                            'new': new_drv})
            _ensure_driver_supports_request(new_drv, router)
            # TODO(kevinbenton): notify old driver explicity of driver change
            with context.session.begin(subtransactions=True):
                self._stm.del_resource_associations(context, [router_id])
                self._stm.add_resource_association(
                    context, 'L3_ROUTER_NAT', new_drv.name, router_id)

    def _get_provider_for_router(self, context, router_id):
        """Return the provider driver handle for a router id."""
        driver_name = self._stm.get_provider_names_by_resource_ids(
            context, [router_id]).get(router_id)
        if not driver_name:
            # this is an old router that hasn't been mapped to a provider
            # yet so we do this now
            router = self.l3_plugin.get_router(context, router_id)
            driver = self._attrs_to_driver(router)
            driver_name = driver.name
            self._stm.add_resource_association(context, 'L3_ROUTER_NAT',
                                               driver_name, router_id)
        return self.drivers[driver_name]

    def _get_provider_for_create(self, context, router):
        """Get provider based on flavor or ha/distributed flags."""
        if not _flavor_specified(router):
            return self._attrs_to_driver(router)
        return self._get_l3_driver_by_flavor(context, router['flavor_id'])

    def _get_l3_driver_by_flavor(self, context, flavor_id):
        """Get a provider driver handle for a given flavor_id."""
        flavor = self._flavor_plugin.get_flavor(context, flavor_id)
        provider = self._flavor_plugin.get_flavor_next_provider(
            context, flavor['id'])[0]
        # TODO(kevinbenton): the callback framework suppresses the nice errors
        # these generate when they fail to lookup. carry them through
        driver = self.drivers[provider['provider']]
        return driver

    def _attrs_to_driver(self, router):
        """Get a provider driver handle based on the ha/distributed flags."""
        distributed = _is_distributed(router['distributed'])
        ha = _is_ha(router['ha'])
        drivers = self.drivers.values()
        # make sure default is tried before the rest if defined
        if self.default_provider:
            drivers.insert(0, self.drivers[self.default_provider])
        for driver in drivers:
            if _is_driver_compatible(distributed, ha, driver):
                return driver
        raise NotImplementedError(
            _("Could not find a service provider that supports "
              "distributed=%(d)s and ha=%(h)s") % {'d': distributed, 'h': ha}
        )


class _LegacyPlusProviderConfiguration(
    provider_configuration.ProviderConfiguration):

    def __init__(self):
        # loads up ha, dvr, and single_node service providers automatically.
        # If an operator has setup explicit values that conflict with these,
        # the operator defined values will take priority.
        super(_LegacyPlusProviderConfiguration, self).__init__()
        for name, driver in (('dvrha', 'dvrha.DvrHaDriver'),
                             ('dvr', 'dvr.DvrDriver'), ('ha', 'ha.HaDriver'),
                             ('single_node', 'single_node.SingleNodeDriver')):
            path = 'neutron.services.l3_router.service_providers.%s' % driver
            try:
                self.add_provider({'service_type': constants.L3_ROUTER_NAT,
                                   'name': name, 'driver': path,
                                   'default': False})
            except lib_exc.Invalid:
                LOG.debug("Could not add L3 provider '%s', it may have "
                          "already been explicitly defined.", name)


def _is_driver_compatible(distributed, ha, driver):
    if not driver.distributed_support.is_compatible(distributed):
        return False
    if not driver.ha_support.is_compatible(ha):
        return False
    return True


def _is_distributed(distributed_attr):
    if distributed_attr is False:
        return False
    if distributed_attr == lib_const.ATTR_NOT_SPECIFIED:
        return cfg.CONF.router_distributed
    return True


def _is_ha(ha_attr):
    if ha_attr is False:
        return False
    if ha_attr == lib_const.ATTR_NOT_SPECIFIED:
        return cfg.CONF.l3_ha
    return True


def _flavor_specified(router):
    return ('flavor_id' in router and
            router['flavor_id'] != lib_const.ATTR_NOT_SPECIFIED)


def _ensure_driver_supports_request(drv, router_body):
    r = router_body
    for key, attr in (('distributed', 'distributed_support'),
                      ('ha', 'ha_support')):
        flag = r.get(key)
        if flag not in [True, False]:
            continue  # not specified in body
        if not getattr(drv, attr).is_compatible(flag):
            raise lib_exc.Invalid(
                _("Provider %(name)s does not support %(key)s=%(flag)s")
                % dict(name=drv.name, key=key, flag=flag))
