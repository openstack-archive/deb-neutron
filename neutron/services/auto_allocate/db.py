# Copyright 2015-2016 Hewlett Packard Enterprise Development Company, LP
#
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

from neutron_lib import exceptions as n_exc
from oslo_db import exception as db_exc
from oslo_log import log as logging
from oslo_utils import excutils
from sqlalchemy import sql

from neutron._i18n import _, _LE
from neutron.api.v2 import attributes
from neutron.callbacks import events
from neutron.callbacks import registry
from neutron.callbacks import resources
from neutron.common import exceptions as c_exc
from neutron.db import api as db_api
from neutron.db import common_db_mixin
from neutron.db import db_base_plugin_v2
from neutron.db import external_net_db
from neutron.db import models_v2
from neutron.db import standard_attr
from neutron.extensions import l3
from neutron import manager
from neutron.plugins.common import constants
from neutron.plugins.common import utils as p_utils
from neutron.services.auto_allocate import exceptions
from neutron.services.auto_allocate import models

LOG = logging.getLogger(__name__)
IS_DEFAULT = 'is_default'
CHECK_REQUIREMENTS = 'dry-run'


def _extend_external_network_default(core_plugin, net_res, net_db):
    """Add is_default field to 'show' response."""
    if net_db.external is not None:
        net_res[IS_DEFAULT] = net_db.external.is_default
    return net_res


def _ensure_external_network_default_value_callback(
    resource, event, trigger, context, request, network):
    """Ensure the is_default db field matches the create/update request."""
    is_default = request.get(IS_DEFAULT, False)
    if event in (events.BEFORE_CREATE, events.BEFORE_UPDATE) and is_default:
        # ensure there is only one default external network at any given time
        obj = (context.session.query(external_net_db.ExternalNetwork).
            filter_by(is_default=True)).first()
        if obj and network['id'] != obj.network_id:
            raise exceptions.DefaultExternalNetworkExists(
                net_id=obj.network_id)

    # Reflect the status of the is_default on the create/update request
    obj = (context.session.query(external_net_db.ExternalNetwork).
        filter_by(network_id=network['id']))
    obj.update({IS_DEFAULT: is_default})


class AutoAllocatedTopologyMixin(common_db_mixin.CommonDbMixin):

    db_base_plugin_v2.NeutronDbPluginV2.register_dict_extend_funcs(
        attributes.NETWORKS, [_extend_external_network_default])

    def __new__(cls, *args, **kwargs):
        # NOTE(kevinbenton): we subscribe on object construction because
        # the tests blow away the callback manager for each run
        new = super(AutoAllocatedTopologyMixin, cls).__new__(cls, *args,
                                                             **kwargs)
        registry.subscribe(_ensure_external_network_default_value_callback,
            resources.EXTERNAL_NETWORK, events.BEFORE_CREATE)
        registry.subscribe(_ensure_external_network_default_value_callback,
            resources.EXTERNAL_NETWORK, events.AFTER_CREATE)
        registry.subscribe(_ensure_external_network_default_value_callback,
            resources.EXTERNAL_NETWORK, events.BEFORE_UPDATE)
        return new

    # TODO(armax): if a tenant modifies auto allocated resources under
    # the hood the behavior of the get_auto_allocated_topology API is
    # undetermined. Consider adding callbacks to deal with the following
    # situations:
    # - insert subnet -> plug router interface
    # - delete router -> remove the entire topology
    # - update subnet -> prevent operation
    # - update router gateway -> prevent operation
    # - ...

    @property
    def core_plugin(self):
        if not getattr(self, '_core_plugin', None):
            self._core_plugin = manager.NeutronManager.get_plugin()
        return self._core_plugin

    @property
    def l3_plugin(self):
        if not getattr(self, '_l3_plugin', None):
            self._l3_plugin = manager.NeutronManager.get_service_plugins().get(
                constants.L3_ROUTER_NAT)
        return self._l3_plugin

    def get_auto_allocated_topology(self, context, tenant_id, fields=None):
        """Return tenant's network associated to auto-allocated topology.

        The topology will be provisioned upon return, if network is missing.
        """
        fields = fields or []
        tenant_id = self._validate(context, tenant_id)
        if CHECK_REQUIREMENTS in fields:
            # for dry-run requests, simply validates that subsequent
            # requests can be fulfilled based on a set of requirements
            # such as existence of default networks, pools, etc.
            return self._check_requirements(context, tenant_id)
        elif fields:
            raise n_exc.BadRequest(resource='auto_allocate',
                msg=_("Unrecognized field"))

        # Check for an existent topology
        network_id = self._get_auto_allocated_network(context, tenant_id)
        if network_id:
            return self._response(network_id, tenant_id, fields=fields)
        # See if we indeed have an external network to connect to, otherwise
        # we will fail fast
        default_external_network = self._get_default_external_network(
            context)

        # If we reach this point, then we got some work to do!
        network_id = self._build_topology(
            context, tenant_id, default_external_network)
        return self._response(network_id, tenant_id, fields=fields)

    def delete_auto_allocated_topology(self, context, tenant_id):
        tenant_id = self._validate(context, tenant_id)
        topology = self._get_auto_allocated_topology(context, tenant_id)
        if topology:
            subnets = self.core_plugin.get_subnets(
                context,
                filters={'network_id': [topology['network_id']]})
            self._cleanup(
                context, network_id=topology['network_id'],
                router_id=topology['router_id'], subnets=subnets)

    def _build_topology(self, context, tenant_id, default_external_network):
        """Build the network topology and returns its network UUID."""
        network_id = None
        router_id = None
        subnets = None
        try:
            subnets = self._provision_tenant_private_network(
                context, tenant_id)
            network_id = subnets[0]['network_id']
            router = self._provision_external_connectivity(
                context, default_external_network, subnets, tenant_id)
            network_id = self._save(
                context, tenant_id, network_id, router['id'], subnets)
            return network_id
        except Exception as e:
            with excutils.save_and_reraise_exception():
                # FIXME(armax): defensively catch all errors and let
                # the caller retry the operation, if it can be retried.
                # This catch-all should no longer be necessary once
                # bug #1612798 is solved; any other error should just
                # surface up to the user and be dealt with as a bug.
                if db_api.is_retriable(e):
                    self._cleanup(
                        context, network_id=network_id,
                        router_id=router_id, subnets=subnets)

    def _check_requirements(self, context, tenant_id):
        """Raise if requirements are not met."""
        self._get_default_external_network(context)
        try:
            self._get_supported_subnetpools(context)
        except n_exc.NotFound:
            raise exceptions.AutoAllocationFailure(
                reason=_("No default subnetpools defined"))
        return {'id': 'dry-run=pass', 'tenant_id': tenant_id}

    def _validate(self, context, tenant_id):
        """Validate and return the tenant to be associated to the topology."""
        if tenant_id == 'None':
            # NOTE(HenryG): the client might be sending us astray by
            # passing no tenant; this is really meant to be the tenant
            # issuing the request, therefore let's get it from the context
            tenant_id = context.tenant_id

        if not context.is_admin and tenant_id != context.tenant_id:
            raise n_exc.NotAuthorized()

        return tenant_id

    def _get_auto_allocated_topology(self, context, tenant_id):
        """Return the auto allocated topology record if present or None."""
        with context.session.begin(subtransactions=True):
            return (context.session.query(models.AutoAllocatedTopology).
                filter_by(tenant_id=tenant_id).first())

    def _get_auto_allocated_network(self, context, tenant_id):
        """Get the auto allocated network for the tenant."""
        network = self._get_auto_allocated_topology(context, tenant_id)
        if network:
            return network['network_id']

    def _response(self, network_id, tenant_id, fields=None):
        """Build response for auto-allocated network."""
        res = {
            'id': network_id,
            'tenant_id': tenant_id
        }
        return self._fields(res, fields)

    def _get_default_external_network(self, context):
        """Get the default external network for the deployment."""
        with context.session.begin(subtransactions=True):
            default_external_networks = (context.session.query(
                external_net_db.ExternalNetwork).
                filter_by(is_default=sql.true()).
                join(models_v2.Network).
                join(standard_attr.StandardAttribute).
                order_by(standard_attr.StandardAttribute.id).all())

        if not default_external_networks:
            LOG.error(_LE("Unable to find default external network "
                          "for deployment, please create/assign one to "
                          "allow auto-allocation to work correctly."))
            raise exceptions.AutoAllocationFailure(
                reason=_("No default router:external network"))
        if len(default_external_networks) > 1:
            LOG.error(_LE("Multiple external default networks detected. "
                          "Network %s is true 'default'."),
                      default_external_networks[0]['network_id'])
        return default_external_networks[0]

    def _get_supported_subnetpools(self, context):
        """Return the default subnet pools available."""
        default_subnet_pools = [
            self.core_plugin.get_default_subnetpool(
                context, ver) for ver in (4, 6)
        ]
        available_pools = [
            s for s in default_subnet_pools if s
        ]
        if not available_pools:
            LOG.error(_LE("No default pools available"))
            raise n_exc.NotFound()

        return available_pools

    def _provision_tenant_private_network(self, context, tenant_id):
        """Create a tenant private network/subnets."""
        network = None
        try:
            network_args = {
                'name': 'auto_allocated_network',
                'admin_state_up': False,
                'tenant_id': tenant_id,
                'shared': False
            }
            network = p_utils.create_network(
                self.core_plugin, context, {'network': network_args})
            subnets = []
            for pool in self._get_supported_subnetpools(context):
                subnet_args = {
                    'name': 'auto_allocated_subnet_v%s' % pool['ip_version'],
                    'network_id': network['id'],
                    'tenant_id': tenant_id,
                    'ip_version': pool['ip_version'],
                    'subnetpool_id': pool['id'],
                }
                subnets.append(p_utils.create_subnet(
                    self.core_plugin, context, {'subnet': subnet_args}))
            return subnets
        except (c_exc.SubnetAllocationError, ValueError,
                n_exc.BadRequest, n_exc.NotFound) as e:
            LOG.error(_LE("Unable to auto allocate topology for tenant "
                          "%(tenant_id)s due to missing or unmet "
                          "requirements. Reason: %(reason)s"),
                      {'tenant_id': tenant_id, 'reason': e})
            if network:
                self._cleanup(context, network['id'])
            raise exceptions.AutoAllocationFailure(
                reason=_("Unable to provide tenant private network"))

    def _provision_external_connectivity(
        self, context, default_external_network, subnets, tenant_id):
        """Uplink tenant subnet(s) to external network."""
        router_args = {
            'name': 'auto_allocated_router',
            l3.EXTERNAL_GW_INFO: default_external_network,
            'tenant_id': tenant_id,
            'admin_state_up': True
        }
        router = None
        attached_subnets = []
        try:
            router = self.l3_plugin.create_router(
                context, {'router': router_args})
            for subnet in subnets:
                self.l3_plugin.add_router_interface(
                    context, router['id'], {'subnet_id': subnet['id']})
                attached_subnets.append(subnet)
            return router
        except n_exc.BadRequest as e:
            LOG.error(_LE("Unable to auto allocate topology for tenant "
                          "%(tenant_id)s because of router errors. "
                          "Reason: %(reason)s"),
                      {'tenant_id': tenant_id, 'reason': e})
            if router:
                router_id = router['id']
            else:
                router_id = None
            self._cleanup(context,
                network_id=subnets[0]['network_id'],
                router_id=router_id, subnets=attached_subnets)
            raise exceptions.AutoAllocationFailure(
                reason=_("Unable to provide external connectivity"))

    def _save(self, context, tenant_id, network_id, router_id, subnets):
        """Save auto-allocated topology, or revert in case of DB errors."""
        try:
            # NOTE(armax): saving the auto allocated topology in a
            # separate transaction will keep the Neutron DB and the
            # Neutron plugin backend in sync, thus allowing for a
            # more bullet proof cleanup. Any other error will have
            # to bubble up.
            with context.session.begin(subtransactions=True):
                context.session.add(
                    models.AutoAllocatedTopology(
                        tenant_id=tenant_id,
                        network_id=network_id,
                        router_id=router_id))
            p_utils.update_network(
                self.core_plugin, context,
                network_id, {'admin_state_up': True})
        except db_exc.DBDuplicateEntry:
            LOG.error(_LE("Multiple auto-allocated networks detected for "
                          "tenant %(tenant)s. Attempting clean up for "
                          "network %(network)s and router %(router)s"),
                      {'tenant': tenant_id,
                       'network': network_id,
                       'router': router_id})
            self._cleanup(
                context, network_id=network_id,
                router_id=router_id, subnets=subnets)
            network_id = self._get_auto_allocated_network(context, tenant_id)
        return network_id

    # FIXME(kevinbenton): get rid of the retry once bug/1612798 is resolved
    @db_api.retry_db_errors
    def _cleanup(self, context, network_id=None, router_id=None, subnets=None):
        """Clean up auto allocated resources."""
        # Concurrent attempts to delete the topology may interleave and
        # cause some operations to fail with NotFound exceptions. Rather
        # than fail partially, the exceptions should be ignored and the
        # cleanup should proceed uninterrupted.
        if router_id:
            for subnet in subnets or []:
                ignore_notfound(
                    self.l3_plugin.remove_router_interface,
                    context, router_id, {'subnet_id': subnet['id']})
            ignore_notfound(self.l3_plugin.delete_router, context, router_id)

        if network_id:
            ignore_notfound(
                self.core_plugin.delete_network, context, network_id)


def ignore_notfound(func, *args, **kwargs):
    """Call the given function and pass if a `NotFound` exception is raised."""
    try:
        return func(*args, **kwargs)
    except n_exc.NotFound:
        pass
