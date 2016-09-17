#    (c) Copyright 2014 Hewlett-Packard Development Company, L.P.
#    All Rights Reserved.
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

from neutron_lib import constants as n_const
from oslo_log import log as logging
from sqlalchemy import or_

from neutron.callbacks import events
from neutron.callbacks import registry
from neutron.callbacks import resources
from neutron.common import utils as n_utils

from neutron.db import agentschedulers_db
from neutron.db import l3_agentschedulers_db as l3agent_sch_db
from neutron.db import models_v2
from neutron.extensions import portbindings
from neutron import manager
from neutron.plugins.common import constants as service_constants
from neutron.plugins.ml2 import db as ml2_db
from neutron.plugins.ml2 import models as ml2_models

LOG = logging.getLogger(__name__)


class L3_DVRsch_db_mixin(l3agent_sch_db.L3AgentSchedulerDbMixin):
    """Mixin class for L3 DVR scheduler.

    DVR currently supports the following use cases:

     - East/West (E/W) traffic between VMs: this is handled in a
       distributed manner across Compute Nodes without a centralized element.
       This includes E/W traffic between VMs on the same Compute Node.
     - North/South traffic for Floating IPs (FIP N/S): this is supported on the
       distributed routers on Compute Nodes without any centralized element.
     - North/South traffic for SNAT (SNAT N/S): this is supported via a
       centralized element that handles the SNAT traffic.

    To support these use cases,  DVR routers rely on an L3 agent that runs on a
    central node (also known as Network Node or Service Node),  as well as, L3
    agents that run individually on each Compute Node of an OpenStack cloud.

    Each L3 agent creates namespaces to route traffic according to the use
    cases outlined above.  The mechanism adopted for creating and managing
    these namespaces is via (Router,  Agent) binding and Scheduling in general.

    The main difference between distributed routers and centralized ones is
    that in the distributed case,  multiple bindings will exist,  one for each
    of the agents participating in the routed topology for the specific router.

    These bindings are created in the following circumstances:

    - A subnet is added to a router via router-interface-add, and that subnet
      has running VM's deployed in it.  A binding will be created between the
      router and any L3 agent whose Compute Node is hosting the VM(s).
    - An external gateway is set to a router via router-gateway-set.  A binding
      will be created between the router and the L3 agent running centrally
      on the Network Node.

    Therefore,  any time a router operation occurs (create, update or delete),
    scheduling will determine whether the router needs to be associated to an
    L3 agent, just like a regular centralized router, with the difference that,
    in the distributed case,  the bindings required are established based on
    the state of the router and the Compute Nodes.
    """

    def dvr_handle_new_service_port(self, context, port, dest_host=None):
        """Handle new dvr service port creation.

        When a new dvr service port is created, this function will
        schedule a dvr router to new compute node if needed and notify
        l3 agent on that node.
        The 'dest_host' will provide the destinaton host of the port in
        case of service port migration.
        """
        port_host = dest_host or port[portbindings.HOST_ID]
        l3_agent_on_host = (self.get_l3_agents(
            context, filters={'host': [port_host]}) or [None])[0]
        if not l3_agent_on_host:
            return

        if dest_host:
            # Make sure we create the floatingip agent gateway port
            # for the destination node if fip is associated with this
            # fixed port
            l3plugin = manager.NeutronManager.get_service_plugins().get(
                service_constants.L3_ROUTER_NAT)
            (
                l3plugin.
                check_for_fip_and_create_agent_gw_port_on_host_if_not_exists(
                    context, port, dest_host))

        subnet_ids = [ip['subnet_id'] for ip in port['fixed_ips']]
        router_ids = self.get_dvr_routers_by_subnet_ids(context, subnet_ids)
        if router_ids:
            LOG.debug('DVR: Handle new service port, host %(host)s, '
                      'router ids %(router_ids)s',
                {'host': port_host, 'router_ids': router_ids})
            self.l3_rpc_notifier.routers_updated_on_host(
                context, router_ids, port_host)

    def get_dvr_routers_by_subnet_ids(self, context, subnet_ids):
        """Gets the dvr routers on vmport subnets."""
        if not subnet_ids:
            return set()

        router_ids = set()
        filter_sub = {'fixed_ips': {'subnet_id': subnet_ids},
                      'device_owner':
                      [n_const.DEVICE_OWNER_DVR_INTERFACE]}
        subnet_ports = self._core_plugin.get_ports(
            context, filters=filter_sub)
        for subnet_port in subnet_ports:
            router_ids.add(subnet_port['device_id'])
        return router_ids

    def get_subnet_ids_on_router(self, context, router_id):
        """Return subnet IDs for interfaces attached to the given router."""
        subnet_ids = set()
        filter_rtr = {'device_id': [router_id]}
        int_ports = self._core_plugin.get_ports(context, filters=filter_rtr)
        for int_port in int_ports:
            int_ips = int_port['fixed_ips']
            if int_ips:
                int_subnet = int_ips[0]['subnet_id']
                subnet_ids.add(int_subnet)
            else:
                LOG.debug('DVR: Could not find a subnet id '
                          'for router %s', router_id)
        return subnet_ids

    def get_dvr_routers_to_remove(self, context, deleted_port):
        """Returns info about which routers should be removed

        In case dvr serviceable port was deleted we need to check
        if any dvr routers should be removed from l3 agent on port's host
        """
        if not n_utils.is_dvr_serviced(deleted_port['device_owner']):
            return []

        admin_context = context.elevated()
        port_host = deleted_port[portbindings.HOST_ID]
        subnet_ids = [ip['subnet_id'] for ip in deleted_port['fixed_ips']]
        router_ids = self.get_dvr_routers_by_subnet_ids(admin_context,
                                                        subnet_ids)

        if not router_ids:
            LOG.debug('No DVR routers for this DVR port %(port)s '
                      'on host %(host)s', {'port': deleted_port['id'],
                                           'host': port_host})
            return []
        agent = self._get_agent_by_type_and_host(
            context, n_const.AGENT_TYPE_L3, port_host)
        removed_router_info = []
        for router_id in router_ids:
            snat_binding = context.session.query(
                l3agent_sch_db.RouterL3AgentBinding).filter_by(
                    router_id=router_id).filter_by(
                        l3_agent_id=agent.id).first()
            if snat_binding:
                # not removing from the agent hosting SNAT for the router
                continue
            subnet_ids = self.get_subnet_ids_on_router(admin_context,
                                                       router_id)
            if self._check_dvr_serviceable_ports_on_host(
                    admin_context, port_host, subnet_ids):
                continue
            filter_rtr = {'device_id': [router_id],
                          'device_owner':
                          [n_const.DEVICE_OWNER_DVR_INTERFACE]}
            int_ports = self._core_plugin.get_ports(
                admin_context, filters=filter_rtr)
            for port in int_ports:
                dvr_binding = (ml2_db.
                               get_distributed_port_binding_by_host(
                                   context.session, port['id'], port_host))
                if dvr_binding:
                    # unbind this port from router
                    dvr_binding['router_id'] = None
                    dvr_binding.update(dvr_binding)

            info = {'router_id': router_id, 'host': port_host,
                    'agent_id': str(agent.id)}
            removed_router_info.append(info)
            LOG.debug('Router %(router_id)s on host %(host)s to be deleted',
                      info)
        return removed_router_info

    def _get_active_l3_agent_routers_sync_data(self, context, host, agent,
                                               router_ids):
        if n_utils.is_extension_supported(self, n_const.L3_HA_MODE_EXT_ALIAS):
            return self.get_ha_sync_data_for_host(context, host, agent,
                                                  router_ids=router_ids,
                                                  active=True)
        return self._get_dvr_sync_data(context, host, agent,
                                       router_ids=router_ids, active=True)

    def get_hosts_to_notify(self, context, router_id):
        """Returns all hosts to send notification about router update"""
        hosts = super(L3_DVRsch_db_mixin, self).get_hosts_to_notify(
            context, router_id)
        router = self.get_router(context, router_id)
        if router.get('distributed', False):
            dvr_hosts = self._get_dvr_hosts_for_router(context, router_id)
            dvr_hosts = set(dvr_hosts) - set(hosts)
            state = agentschedulers_db.get_admin_state_up_filter()
            agents = self.get_l3_agents(context, active=state,
                                        filters={'host': dvr_hosts})
            hosts += [a.host for a in agents]

        return hosts

    def _get_dvr_hosts_for_router(self, context, router_id):
        """Get a list of hosts where specified DVR router should be hosted

        It will first get IDs of all subnets connected to the router and then
        get a set of hosts where all dvr serviceable ports on those subnets
        are bound
        """
        subnet_ids = self.get_subnet_ids_on_router(context, router_id)
        Binding = ml2_models.PortBinding
        Port = models_v2.Port
        IPAllocation = models_v2.IPAllocation

        query = context.session.query(Binding.host).distinct()
        query = query.join(Binding.port)
        query = query.join(Port.fixed_ips)
        query = query.filter(IPAllocation.subnet_id.in_(subnet_ids))
        owner_filter = or_(
            Port.device_owner.startswith(n_const.DEVICE_OWNER_COMPUTE_PREFIX),
            Port.device_owner.in_(
                n_utils.get_other_dvr_serviced_device_owners()))
        query = query.filter(owner_filter)
        hosts = [item[0] for item in query]
        LOG.debug('Hosts for router %s: %s', router_id, hosts)
        return hosts

    def _get_dvr_subnet_ids_on_host_query(self, context, host):
        query = context.session.query(
            models_v2.IPAllocation.subnet_id).distinct()
        query = query.join(models_v2.IPAllocation.port)
        query = query.join(models_v2.Port.port_binding)
        query = query.filter(ml2_models.PortBinding.host == host)
        owner_filter = or_(
            models_v2.Port.device_owner.startswith(
                n_const.DEVICE_OWNER_COMPUTE_PREFIX),
            models_v2.Port.device_owner.in_(
                n_utils.get_other_dvr_serviced_device_owners()))
        query = query.filter(owner_filter)
        return query

    def _get_dvr_router_ids_for_host(self, context, host):
        subnet_ids_on_host_query = self._get_dvr_subnet_ids_on_host_query(
            context, host)
        query = context.session.query(models_v2.Port.device_id).distinct()
        query = query.filter(
            models_v2.Port.device_owner == n_const.DEVICE_OWNER_DVR_INTERFACE)
        query = query.join(models_v2.Port.fixed_ips)
        query = query.filter(
            models_v2.IPAllocation.subnet_id.in_(subnet_ids_on_host_query))
        router_ids = [item[0] for item in query]
        LOG.debug('DVR routers on host %s: %s', host, router_ids)
        return router_ids

    def _get_router_ids_for_agent(self, context, agent_db, router_ids):
        result_set = set(super(L3_DVRsch_db_mixin,
                            self)._get_router_ids_for_agent(
            context, agent_db, router_ids))
        router_ids = set(router_ids or [])
        if router_ids and result_set == router_ids:
            # no need for extra dvr checks if requested routers are
            # explicitly scheduled to the agent
            return list(result_set)

        # dvr routers are not explicitly scheduled to agents on hosts with
        # dvr serviceable ports, so need special handling
        if self._get_agent_mode(agent_db) in [n_const.L3_AGENT_MODE_DVR,
                                              n_const.L3_AGENT_MODE_DVR_SNAT]:
            if not router_ids:
                result_set |= set(self._get_dvr_router_ids_for_host(
                    context, agent_db['host']))
            else:
                for router_id in (router_ids - result_set):
                    subnet_ids = self.get_subnet_ids_on_router(
                        context, router_id)
                    if (subnet_ids and
                            self._check_dvr_serviceable_ports_on_host(
                                    context, agent_db['host'],
                                    list(subnet_ids))):
                        result_set.add(router_id)

        return list(result_set)

    def _check_dvr_serviceable_ports_on_host(self, context, host, subnet_ids):
        """Check for existence of dvr serviceable ports on host

        :param context: request context
        :param host: host to look ports on
        :param subnet_ids: IDs of subnets to look ports on
        :return: return True if dvr serviceable port exists on host,
                 otherwise return False
        """
        # db query will return ports for all subnets if subnet_ids is empty,
        # so need to check first
        if not subnet_ids:
            return False

        Binding = ml2_models.PortBinding
        IPAllocation = models_v2.IPAllocation
        Port = models_v2.Port

        query = context.session.query(Binding)
        query = query.join(Binding.port)
        query = query.join(Port.fixed_ips)
        query = query.filter(
            IPAllocation.subnet_id.in_(subnet_ids))
        device_filter = or_(
            models_v2.Port.device_owner.startswith(
                n_const.DEVICE_OWNER_COMPUTE_PREFIX),
            models_v2.Port.device_owner.in_(
                n_utils.get_other_dvr_serviced_device_owners()))
        query = query.filter(device_filter)
        host_filter = or_(
            ml2_models.PortBinding.host == host,
            ml2_models.PortBinding.profile.contains(host))
        query = query.filter(host_filter)
        return query.first() is not None


def _dvr_handle_unbound_allowed_addr_pair_add(
        plugin, context, port, allowed_address_pair):
    updated_port = plugin.update_unbound_allowed_address_pair_port_binding(
        context, port, allowed_address_pair)
    if updated_port:
        LOG.debug("Allowed address pair port binding updated "
                  "based on service port binding: %s", updated_port)
        plugin.dvr_handle_new_service_port(context, updated_port)
    plugin.update_arp_entry_for_dvr_service_port(context, port)


def _dvr_handle_unbound_allowed_addr_pair_del(
        plugin, context, port, allowed_address_pair):
    updated_port = plugin.remove_unbound_allowed_address_pair_port_binding(
        context, port, allowed_address_pair)
    if updated_port:
        LOG.debug("Allowed address pair port binding removed "
                  "from service port binding: %s", updated_port)
    aa_fixed_ips = plugin._get_allowed_address_pair_fixed_ips(context, port)
    if aa_fixed_ips:
        plugin.delete_arp_entry_for_dvr_service_port(
            context, port, fixed_ips_to_delete=aa_fixed_ips)


def _notify_l3_agent_new_port(resource, event, trigger, **kwargs):
    LOG.debug('Received %(resource)s %(event)s', {
        'resource': resource,
        'event': event})
    port = kwargs.get('port')
    if not port:
        return

    if n_utils.is_dvr_serviced(port['device_owner']):
        l3plugin = manager.NeutronManager.get_service_plugins().get(
            service_constants.L3_ROUTER_NAT)
        context = kwargs['context']
        l3plugin.dvr_handle_new_service_port(context, port)
        l3plugin.update_arp_entry_for_dvr_service_port(context, port)


def _notify_port_delete(event, resource, trigger, **kwargs):
    context = kwargs['context']
    port = kwargs['port']
    l3plugin = manager.NeutronManager.get_service_plugins().get(
        service_constants.L3_ROUTER_NAT)
    if port:
        port_host = port.get(portbindings.HOST_ID)
        allowed_address_pairs_list = port.get('allowed_address_pairs')
        if allowed_address_pairs_list and port_host:
            for address_pair in allowed_address_pairs_list:
                _dvr_handle_unbound_allowed_addr_pair_del(
                    l3plugin, context, port, address_pair)
    l3plugin.delete_arp_entry_for_dvr_service_port(context, port)
    removed_routers = l3plugin.get_dvr_routers_to_remove(context, port)
    for info in removed_routers:
        l3plugin.l3_rpc_notifier.router_removed_from_agent(
            context, info['router_id'], info['host'])


def _notify_l3_agent_port_update(resource, event, trigger, **kwargs):
    new_port = kwargs.get('port')
    original_port = kwargs.get('original_port')

    if new_port and original_port:
        original_device_owner = original_port.get('device_owner', '')
        new_device_owner = new_port.get('device_owner', '')
        is_new_device_dvr_serviced = n_utils.is_dvr_serviced(new_device_owner)
        l3plugin = manager.NeutronManager.get_service_plugins().get(
                service_constants.L3_ROUTER_NAT)
        context = kwargs['context']
        is_port_no_longer_serviced = (
            n_utils.is_dvr_serviced(original_device_owner) and
            not n_utils.is_dvr_serviced(new_device_owner))
        is_port_moved = (
            original_port[portbindings.HOST_ID] and
            original_port[portbindings.HOST_ID] !=
            new_port[portbindings.HOST_ID])
        if is_port_no_longer_serviced or is_port_moved:
            removed_routers = l3plugin.get_dvr_routers_to_remove(
                context,
                original_port)
            if removed_routers:
                removed_router_args = {
                    'context': context,
                    'port': original_port,
                    'removed_routers': removed_routers,
                }
                _notify_port_delete(
                    event, resource, trigger, **removed_router_args)
            fip = l3plugin._get_floatingip_on_port(context,
                                                   port_id=original_port['id'])
            if fip and not (removed_routers and
                            fip['router_id'] in removed_routers):
                l3plugin.l3_rpc_notifier.routers_updated_on_host(
                    context, [fip['router_id']],
                    original_port[portbindings.HOST_ID])
            if not is_new_device_dvr_serviced:
                return
        is_new_port_binding_changed = (
            new_port[portbindings.HOST_ID] and
            (original_port[portbindings.HOST_ID] !=
                new_port[portbindings.HOST_ID]))
        dest_host = None
        new_port_profile = new_port.get(portbindings.PROFILE)
        if new_port_profile:
            dest_host = new_port_profile.get('migrating_to')
            # This check is required to prevent an arp update
            # of the allowed_address_pair port.
            if new_port_profile.get('original_owner'):
                return
        # If dest_host is set, then the port profile has changed
        # and this port is in migration. The call below will
        # pre-create the router on the new host
        if ((is_new_port_binding_changed or dest_host) and
            is_new_device_dvr_serviced):
            l3plugin.dvr_handle_new_service_port(context, new_port,
                                                 dest_host=dest_host)
            l3plugin.update_arp_entry_for_dvr_service_port(
                context, new_port)
            return
        # Check for allowed_address_pairs and port state
        new_port_host = new_port.get(portbindings.HOST_ID)
        allowed_address_pairs_list = new_port.get('allowed_address_pairs')
        if allowed_address_pairs_list and new_port_host:
            new_port_state = new_port.get('admin_state_up')
            original_port_state = original_port.get('admin_state_up')
            if new_port_state and not original_port_state:
                # Case were we activate the port from inactive state.
                for address_pair in allowed_address_pairs_list:
                    _dvr_handle_unbound_allowed_addr_pair_add(
                        l3plugin, context, new_port, address_pair)
                return
            elif original_port_state and not new_port_state:
                # Case were we deactivate the port from active state.
                for address_pair in allowed_address_pairs_list:
                    _dvr_handle_unbound_allowed_addr_pair_del(
                        l3plugin, context, original_port, address_pair)
                return
            elif new_port_state and original_port_state:
                # Case were the same port has additional address_pairs
                # added.
                for address_pair in allowed_address_pairs_list:
                    _dvr_handle_unbound_allowed_addr_pair_add(
                        l3plugin, context, new_port, address_pair)
                return

        is_fixed_ips_changed = (
            'fixed_ips' in new_port and
            'fixed_ips' in original_port and
            new_port['fixed_ips'] != original_port['fixed_ips'])
        if kwargs.get('mac_address_updated') or is_fixed_ips_changed:
            l3plugin.update_arp_entry_for_dvr_service_port(
                context, new_port)


def subscribe():
    registry.subscribe(
        _notify_l3_agent_port_update, resources.PORT, events.AFTER_UPDATE)
    registry.subscribe(
        _notify_l3_agent_new_port, resources.PORT, events.AFTER_CREATE)
    registry.subscribe(
        _notify_port_delete, resources.PORT, events.AFTER_DELETE)
