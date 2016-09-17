# Copyright (c) 2015 OpenStack Foundation.
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

import collections
import copy
import itertools

import netaddr
from neutron_lib.api import validators
from neutron_lib import constants as const
from neutron_lib import exceptions as exc
from oslo_config import cfg
from oslo_db import exception as db_exc
from oslo_log import log as logging
from sqlalchemy import and_, or_
from sqlalchemy.orm import exc as orm_exc

from neutron._i18n import _, _LI
from neutron.common import constants
from neutron.common import exceptions as n_exc
from neutron.common import ipv6_utils
from neutron.common import utils as common_utils
from neutron.db import db_base_plugin_common
from neutron.db.models import subnet_service_type as sst_model
from neutron.db import models_v2
from neutron.db import segments_db
from neutron.extensions import portbindings
from neutron.extensions import segment
from neutron.ipam import exceptions as ipam_exceptions
from neutron.ipam import utils as ipam_utils
from neutron.objects import subnet as subnet_obj
from neutron.services.segments import db as segment_svc_db
from neutron.services.segments import exceptions as segment_exc

LOG = logging.getLogger(__name__)


class IpamBackendMixin(db_base_plugin_common.DbBasePluginCommon):
    """Contains IPAM specific code which is common for both backends.
    """

    # Tracks changes in ip allocation for port using namedtuple
    Changes = collections.namedtuple('Changes', 'add original remove')

    @staticmethod
    def _gateway_ip_str(subnet, cidr_net):
        if subnet.get('gateway_ip') is const.ATTR_NOT_SPECIFIED:
            return str(netaddr.IPNetwork(cidr_net).network + 1)
        return subnet.get('gateway_ip')

    @staticmethod
    def pools_to_ip_range(ip_pools):
        ip_range_pools = []
        for ip_pool in ip_pools:
            try:
                ip_range_pools.append(netaddr.IPRange(ip_pool['start'],
                                                      ip_pool['end']))
            except netaddr.AddrFormatError:
                LOG.info(_LI("Found invalid IP address in pool: "
                             "%(start)s - %(end)s:"),
                         {'start': ip_pool['start'],
                          'end': ip_pool['end']})
                raise n_exc.InvalidAllocationPool(pool=ip_pool)
        return ip_range_pools

    def delete_subnet(self, context, subnet_id):
        pass

    def validate_pools_with_subnetpool(self, subnet):
        """Verifies that allocation pools are set correctly

        Allocation pools can be set for specific subnet request only
        """
        has_allocpool = validators.is_attr_set(subnet['allocation_pools'])
        is_any_subnetpool_request = not validators.is_attr_set(subnet['cidr'])
        if is_any_subnetpool_request and has_allocpool:
            reason = _("allocation_pools allowed only "
                       "for specific subnet requests.")
            raise exc.BadRequest(resource='subnets', msg=reason)

    def _validate_ip_version_with_subnetpool(self, subnet, subnetpool):
        """Validates ip version for subnet_pool and requested subnet"""
        ip_version = subnet.get('ip_version')
        has_ip_version = validators.is_attr_set(ip_version)
        if has_ip_version and ip_version != subnetpool.ip_version:
            args = {'req_ver': str(subnet['ip_version']),
                    'pool_ver': str(subnetpool.ip_version)}
            reason = _("Cannot allocate IPv%(req_ver)s subnet from "
                       "IPv%(pool_ver)s subnet pool") % args
            raise exc.BadRequest(resource='subnets', msg=reason)

    def _update_db_port(self, context, db_port, new_port, network_id, new_mac):
        # Remove all attributes in new_port which are not in the port DB model
        # and then update the port
        if (new_mac and new_mac != db_port.mac_address and
                self._is_mac_in_use(context, network_id, new_mac)):
            raise exc.MacAddressInUse(net_id=network_id, mac=new_mac)
        db_port.update(self._filter_non_model_columns(new_port,
                                                      models_v2.Port))

    def _update_subnet_host_routes(self, context, id, s):

        def _combine(ht):
            return ht['destination'] + "_" + ht['nexthop']

        old_route_list = self._get_route_by_subnet(context, id)

        new_route_set = set([_combine(route)
                             for route in s['host_routes']])

        old_route_set = set([_combine(route)
                             for route in old_route_list])

        for route_str in old_route_set - new_route_set:
            for route in old_route_list:
                if _combine(route) == route_str:
                    context.session.delete(route)
        for route_str in new_route_set - old_route_set:
            route = models_v2.SubnetRoute(
                destination=route_str.partition("_")[0],
                nexthop=route_str.partition("_")[2],
                subnet_id=id)
            context.session.add(route)

        # Gather host routes for result
        new_routes = []
        for route_str in new_route_set:
            new_routes.append(
                {'destination': route_str.partition("_")[0],
                 'nexthop': route_str.partition("_")[2]})
        del s["host_routes"]
        return new_routes

    def _update_subnet_dns_nameservers(self, context, id, s):
        old_dns_list = self._get_dns_by_subnet(context, id)
        new_dns_addr_list = s["dns_nameservers"]

        # NOTE(changzhi) delete all dns nameservers from db
        # when update subnet's DNS nameservers. And store new
        # nameservers with order one by one.
        for dns in old_dns_list:
            dns.delete()

        for order, server in enumerate(new_dns_addr_list):
            dns = subnet_obj.DNSNameServer(context,
                                           address=server,
                                           order=order,
                                           subnet_id=id)
            dns.create()
        del s["dns_nameservers"]
        return new_dns_addr_list

    def _update_subnet_allocation_pools(self, context, subnet_id, s):
        context.session.query(models_v2.IPAllocationPool).filter_by(
            subnet_id=subnet_id).delete()
        pools = [(netaddr.IPAddress(p.first, p.version).format(),
                  netaddr.IPAddress(p.last, p.version).format())
                 for p in s['allocation_pools']]
        new_pools = [models_v2.IPAllocationPool(first_ip=p[0],
                                                last_ip=p[1],
                                                subnet_id=subnet_id)
                     for p in pools]
        context.session.add_all(new_pools)

        # Gather new pools for result
        result_pools = [{'start': p[0], 'end': p[1]} for p in pools]
        del s['allocation_pools']
        return result_pools

    def _update_subnet_service_types(self, context, subnet_id, s):
        old_types = context.session.query(
            sst_model.SubnetServiceType).filter_by(subnet_id=subnet_id)
        for service_type in old_types:
            context.session.delete(service_type)
        updated_types = s.pop('service_types')
        for service_type in updated_types:
            new_type = sst_model.SubnetServiceType(
                           subnet_id=subnet_id,
                           service_type=service_type)
            context.session.add(new_type)
        return updated_types

    def update_db_subnet(self, context, subnet_id, s, oldpools):
        changes = {}
        if "dns_nameservers" in s:
            changes['dns_nameservers'] = (
                self._update_subnet_dns_nameservers(context, subnet_id, s))

        if "host_routes" in s:
            changes['host_routes'] = self._update_subnet_host_routes(
                context, subnet_id, s)

        if "allocation_pools" in s:
            changes['allocation_pools'] = (
                self._update_subnet_allocation_pools(context, subnet_id, s))

        if "service_types" in s:
            changes['service_types'] = (
                self._update_subnet_service_types(context, subnet_id, s))

        subnet = self._get_subnet(context, subnet_id)
        subnet.update(s)
        return subnet, changes

    def _validate_subnet_cidr(self, context, network, new_subnet_cidr):
        """Validate the CIDR for a subnet.

        Verifies the specified CIDR does not overlap with the ones defined
        for the other subnets specified for this network, or with any other
        CIDR if overlapping IPs are disabled. Does not apply to subnets with
        temporary IPv6 Prefix Delegation CIDRs (::/64).
        """
        new_subnet_ipset = netaddr.IPSet([new_subnet_cidr])
        # Disallow subnets with prefix length 0 as they will lead to
        # dnsmasq failures (see bug 1362651).
        # This is not a discrimination against /0 subnets.
        # A /0 subnet is conceptually possible but hardly a practical
        # scenario for neutron's use cases.
        for cidr in new_subnet_ipset.iter_cidrs():
            if cidr.prefixlen == 0:
                err_msg = _("0 is not allowed as CIDR prefix length")
                raise exc.InvalidInput(error_message=err_msg)

        if cfg.CONF.allow_overlapping_ips:
            subnet_list = network.subnets
        else:
            subnet_list = self._get_all_subnets(context)
        for subnet in subnet_list:
            if ((netaddr.IPSet([subnet.cidr]) & new_subnet_ipset) and
                subnet.cidr != constants.PROVISIONAL_IPV6_PD_PREFIX):
                # don't give out details of the overlapping subnet
                err_msg = (_("Requested subnet with cidr: %(cidr)s for "
                             "network: %(network_id)s overlaps with another "
                             "subnet") %
                           {'cidr': new_subnet_cidr,
                            'network_id': network.id})
                LOG.info(_LI("Validation for CIDR: %(new_cidr)s failed - "
                             "overlaps with subnet %(subnet_id)s "
                             "(CIDR: %(cidr)s)"),
                         {'new_cidr': new_subnet_cidr,
                          'subnet_id': subnet.id,
                          'cidr': subnet.cidr})
                raise exc.InvalidInput(error_message=err_msg)

    def _validate_network_subnetpools(self, network,
                                      new_subnetpool_id, ip_version):
        """Validate all subnets on the given network have been allocated from
           the same subnet pool as new_subnetpool_id
        """
        for subnet in network.subnets:
            if (subnet.ip_version == ip_version and
                    new_subnetpool_id != subnet.subnetpool_id):
                raise n_exc.NetworkSubnetPoolAffinityError()

    def validate_allocation_pools(self, ip_pools, subnet_cidr):
        """Validate IP allocation pools.

        Verify start and end address for each allocation pool are valid,
        ie: constituted by valid and appropriately ordered IP addresses.
        Also, verify pools do not overlap among themselves.
        Finally, verify that each range fall within the subnet's CIDR.
        """
        subnet = netaddr.IPNetwork(subnet_cidr)
        subnet_first_ip = netaddr.IPAddress(subnet.first + 1)
        # last address is broadcast in v4
        subnet_last_ip = netaddr.IPAddress(subnet.last - (subnet.version == 4))

        LOG.debug("Performing IP validity checks on allocation pools")
        ip_sets = []
        for ip_pool in ip_pools:
            start_ip = netaddr.IPAddress(ip_pool.first, ip_pool.version)
            end_ip = netaddr.IPAddress(ip_pool.last, ip_pool.version)
            if (start_ip.version != subnet.version or
                    end_ip.version != subnet.version):
                LOG.info(_LI("Specified IP addresses do not match "
                             "the subnet IP version"))
                raise n_exc.InvalidAllocationPool(pool=ip_pool)
            if start_ip < subnet_first_ip or end_ip > subnet_last_ip:
                LOG.info(_LI("Found pool larger than subnet "
                             "CIDR:%(start)s - %(end)s"),
                         {'start': start_ip, 'end': end_ip})
                raise n_exc.OutOfBoundsAllocationPool(
                    pool=ip_pool,
                    subnet_cidr=subnet_cidr)
            # Valid allocation pool
            # Create an IPSet for it for easily verifying overlaps
            ip_sets.append(netaddr.IPSet(ip_pool.cidrs()))

        LOG.debug("Checking for overlaps among allocation pools "
                  "and gateway ip")
        ip_ranges = ip_pools[:]

        # Use integer cursors as an efficient way for implementing
        # comparison and avoiding comparing the same pair twice
        for l_cursor in range(len(ip_sets)):
            for r_cursor in range(l_cursor + 1, len(ip_sets)):
                if ip_sets[l_cursor] & ip_sets[r_cursor]:
                    l_range = ip_ranges[l_cursor]
                    r_range = ip_ranges[r_cursor]
                    LOG.info(_LI("Found overlapping ranges: %(l_range)s and "
                                 "%(r_range)s"),
                             {'l_range': l_range, 'r_range': r_range})
                    raise n_exc.OverlappingAllocationPools(
                        pool_1=l_range,
                        pool_2=r_range,
                        subnet_cidr=subnet_cidr)

    def _validate_max_ips_per_port(self, fixed_ip_list, device_owner):
        if common_utils.is_port_trusted({'device_owner': device_owner}):
            return

        if len(fixed_ip_list) > cfg.CONF.max_fixed_ips_per_port:
            msg = _('Exceeded maximum amount of fixed ips per port.')
            raise exc.InvalidInput(error_message=msg)

    def _validate_segment(self, context, network_id, segment_id):
        query = context.session.query(models_v2.Subnet.segment_id)
        query = query.filter(models_v2.Subnet.network_id == network_id)
        associated_segments = set(row.segment_id for row in query)
        if None in associated_segments and len(associated_segments) > 1:
            raise segment_exc.SubnetsNotAllAssociatedWithSegments(
                network_id=network_id)

        if segment_id:
            query = context.session.query(segments_db.NetworkSegment)
            query = query.filter(segments_db.NetworkSegment.id == segment_id)
            segment_model = query.one()
            if segment_model.network_id != network_id:
                raise segment_exc.NetworkIdsDontMatch(
                    subnet_network=network_id,
                    segment_id=segment_id)
            if segment_model.is_dynamic:
                raise segment_exc.SubnetCantAssociateToDynamicSegment()

    def _get_subnet_for_fixed_ip(self, context, fixed, subnets):
        # Subnets are all the subnets belonging to the same network.
        if not subnets:
            msg = _('IP allocation requires subnets for network')
            raise exc.InvalidInput(error_message=msg)

        if 'subnet_id' in fixed:
            def get_matching_subnet():
                for subnet in subnets:
                    if subnet['id'] == fixed['subnet_id']:
                        return subnet
            subnet = get_matching_subnet()
            if not subnet:
                subnet = self._get_subnet(context, fixed['subnet_id'])
                msg = (_("Failed to create port on network %(network_id)s"
                         ", because fixed_ips included invalid subnet "
                         "%(subnet_id)s") %
                       {'network_id': subnet['network_id'],
                        'subnet_id': fixed['subnet_id']})
                raise exc.InvalidInput(error_message=msg)
            # Ensure that the IP is valid on the subnet
            if ('ip_address' in fixed and
                not ipam_utils.check_subnet_ip(subnet['cidr'],
                                               fixed['ip_address'])):
                raise exc.InvalidIpForSubnet(ip_address=fixed['ip_address'])
            return subnet

        if 'ip_address' not in fixed:
            msg = _('IP allocation requires subnet_id or ip_address')
            raise exc.InvalidInput(error_message=msg)

        for subnet in subnets:
            if ipam_utils.check_subnet_ip(subnet['cidr'],
                                          fixed['ip_address']):
                return subnet
        raise exc.InvalidIpForNetwork(ip_address=fixed['ip_address'])

    def generate_pools(self, cidr, gateway_ip):
        return ipam_utils.generate_pools(cidr, gateway_ip)

    def _prepare_allocation_pools(self, allocation_pools, cidr, gateway_ip):
        """Returns allocation pools represented as list of IPRanges"""
        if not validators.is_attr_set(allocation_pools):
            return self.generate_pools(cidr, gateway_ip)

        ip_range_pools = self.pools_to_ip_range(allocation_pools)
        self.validate_allocation_pools(ip_range_pools, cidr)
        if gateway_ip:
            self.validate_gw_out_of_pools(gateway_ip, ip_range_pools)
        return ip_range_pools

    def validate_gw_out_of_pools(self, gateway_ip, pools):
        for pool_range in pools:
            if netaddr.IPAddress(gateway_ip) in pool_range:
                raise n_exc.GatewayConflictWithAllocationPools(
                    pool=pool_range,
                    ip_address=gateway_ip)

    def _is_ip_required_by_subnet(self, context, subnet_id, device_owner):
        # For ports that are not router ports, retain any automatic
        # (non-optional, e.g. IPv6 SLAAC) addresses.
        # NOTE: Need to check the SNAT ports for DVR routers here since
        # they consume an IP.
        if device_owner in const.ROUTER_INTERFACE_OWNERS_SNAT:
            return True

        subnet = self._get_subnet(context, subnet_id)
        return not (ipv6_utils.is_auto_address_subnet(subnet) and
                    not ipv6_utils.is_ipv6_pd_enabled(subnet))

    def _get_changed_ips_for_port(self, context, original_ips,
                                  new_ips, device_owner):
        """Calculate changes in IPs for the port."""
        # Collect auto addressed subnet ids that has to be removed on update
        delete_subnet_ids = set(ip['subnet_id'] for ip in new_ips
                                if ip.get('delete_subnet'))
        ips = [ip for ip in new_ips
               if ip.get('subnet_id') not in delete_subnet_ids]
        # the new_ips contain all of the fixed_ips that are to be updated
        self._validate_max_ips_per_port(ips, device_owner)

        add_ips = []
        remove_ips = []

        ips_map = {ip['ip_address']: ip
                   for ip in itertools.chain(new_ips, original_ips)
                   if 'ip_address' in ip}

        new = set()
        for ip in new_ips:
            if ip.get('subnet_id') not in delete_subnet_ids:
                if 'ip_address' in ip:
                    new.add(ip['ip_address'])
                else:
                    add_ips.append(ip)

        # Convert original ip addresses to sets
        orig = set(ip['ip_address'] for ip in original_ips)

        add = new - orig
        unchanged = new & orig
        remove = orig - new

        # Convert results back to list of dicts
        add_ips += [ips_map[ip] for ip in add]
        prev_ips = [ips_map[ip] for ip in unchanged]

        # Mark ip for removing if it is not found in new_ips
        # and subnet requires ip to be set manually.
        # For auto addressed subnet leave ip unchanged
        # unless it is explicitly marked for delete.
        for ip in remove:
            subnet_id = ips_map[ip]['subnet_id']
            ip_required = self._is_ip_required_by_subnet(context, subnet_id,
                                                         device_owner)
            if ip_required or subnet_id in delete_subnet_ids:
                remove_ips.append(ips_map[ip])
            else:
                prev_ips.append(ips_map[ip])

        return self.Changes(add=add_ips,
                            original=prev_ips,
                            remove=remove_ips)

    def delete_port(self, context, port_id):
        query = (context.session.query(models_v2.Port).
                 enable_eagerloads(False).filter_by(id=port_id))
        # Use of the ORM mapper is needed for ensuring appropriate resource
        # tracking; otherwise SQL Alchemy events won't be triggered.
        # For more info check 'caveats' in doc/source/devref/quota.rst
        try:
            context.session.delete(query.first())
        except orm_exc.UnmappedInstanceError:
            LOG.debug("Port %s was not found and therefore no delete "
                      "operation was performed", port_id)

    def _save_subnet(self, context,
                     network,
                     subnet_args,
                     dns_nameservers,
                     host_routes,
                     subnet_request):
        self._validate_subnet_cidr(context, network, subnet_args['cidr'])
        self._validate_network_subnetpools(network,
                                           subnet_args['subnetpool_id'],
                                           subnet_args['ip_version'])

        service_types = subnet_args.pop('service_types', [])

        subnet = models_v2.Subnet(**subnet_args)
        segment_id = subnet_args.get('segment_id')
        try:
            context.session.add(subnet)
            context.session.flush()
        except db_exc.DBReferenceError:
            raise segment_exc.SegmentNotFound(segment_id=segment_id)
        self._validate_segment(context, network['id'], segment_id)

        # NOTE(changzhi) Store DNS nameservers with order into DB one
        # by one when create subnet with DNS nameservers
        if validators.is_attr_set(dns_nameservers):
            for order, server in enumerate(dns_nameservers):
                dns = subnet_obj.DNSNameServer(context,
                                               address=server,
                                               order=order,
                                               subnet_id=subnet.id)
                dns.create()

        if validators.is_attr_set(host_routes):
            for rt in host_routes:
                route = models_v2.SubnetRoute(
                    subnet_id=subnet.id,
                    destination=rt['destination'],
                    nexthop=rt['nexthop'])
                context.session.add(route)

        if validators.is_attr_set(service_types):
            for service_type in service_types:
                service_type_entry = sst_model.SubnetServiceType(
                    subnet_id=subnet.id,
                    service_type=service_type)
                context.session.add(service_type_entry)

        self.save_allocation_pools(context, subnet,
                                   subnet_request.allocation_pools)

        return subnet

    def _classify_subnets(self, context, subnets):
        """Split into v4, v6 stateless and v6 stateful subnets"""

        v4, v6_stateful, v6_stateless = [], [], []
        for subnet in subnets:
            if subnet['ip_version'] == 4:
                v4.append(subnet)
            elif not ipv6_utils.is_auto_address_subnet(subnet):
                v6_stateful.append(subnet)
            else:
                v6_stateless.append(subnet)
        return v4, v6_stateful, v6_stateless

    def _update_ips_for_pd_subnet(self, context, subnets,
                                  fixed_ips, mac_address=None):
        fixed_ip_list = []
        subnet_set = {fixed['subnet_id'] for fixed in fixed_ips
                      if 'subnet_id' in fixed}
        pd_subnets = [s for s in subnets
                      if (s['id'] in subnet_set and
                          ipv6_utils.is_ipv6_pd_enabled(s))]
        for subnet in pd_subnets:
            # Already checked subnet validity in _get_subnet_for_fixed_ip
            if mac_address:
                fixed_ip_list.append({'subnet_id': subnet['id'],
                                      'subnet_cidr': subnet['cidr'],
                                      'eui64_address': True,
                                      'mac': mac_address})
            else:
                fixed_ip_list.append({'subnet_id': subnet['id']})
        return fixed_ip_list

    def _query_filter_service_subnets(self, query, service_type):
        ServiceType = sst_model.SubnetServiceType
        query = query.add_entity(ServiceType)
        query = query.outerjoin(ServiceType)
        query = query.filter(or_(ServiceType.service_type.is_(None),
                                 ServiceType.service_type == service_type))
        return query.from_self(models_v2.Subnet)

    def _check_service_subnets(self, query, service_type):
        """Raise an exception if empty subnet list is caused by service type"""
        if not query.limit(1).count():
            return
        query = self._query_filter_service_subnets(query, service_type)
        if query.limit(1).count():
            return
        raise ipam_exceptions.IpAddressGenerationFailureNoMatchingSubnet()

    def _sort_service_subnets(self, subnets, query, service_type):
        """Give priority to subnets with service_types"""
        subnets = sorted(subnets,
                         key=lambda subnet: not subnet.get('service_types'))
        if not subnets:
            # If we have an empty subnet list, check if it's caused by
            # the service type.
            self._check_service_subnets(query, service_type)
        return subnets

    def _ipam_get_subnets(self, context, network_id, host, service_type=None):
        Subnet = models_v2.Subnet
        SegmentHostMapping = segment_svc_db.SegmentHostMapping

        unfiltered_query = self._get_collection_query(context, Subnet)
        unfiltered_query = unfiltered_query.filter(
                               Subnet.network_id == network_id)
        query = self._query_filter_service_subnets(unfiltered_query,
                                                   service_type)
        # Note:  This seems redundant, but its not.  It has to cover cases
        # where host is None, ATTR_NOT_SPECIFIED, or '' due to differences in
        # host binding implementations.
        if not validators.is_attr_set(host) or not host:
            query = query.filter(Subnet.segment_id.is_(None))
            return self._sort_service_subnets(
                       [self._make_subnet_dict(c, context=context)
                        for c in query], unfiltered_query, service_type)

        # A host has been provided.  Consider these two scenarios
        # 1. Not a routed network:  subnets are not on segments
        # 2. Is a routed network:  only subnets on segments mapped to host
        # The following join query returns results for either.  The two are
        # guaranteed to be mutually exclusive when subnets are created.
        query = query.add_entity(SegmentHostMapping)
        query = query.outerjoin(
            SegmentHostMapping,
            and_(Subnet.segment_id == SegmentHostMapping.segment_id,
                 SegmentHostMapping.host == host))
        # Essentially "segment_id IS NULL XNOR host IS NULL"
        query = query.filter(or_(and_(Subnet.segment_id.isnot(None),
                                      SegmentHostMapping.host.isnot(None)),
                                 and_(Subnet.segment_id.is_(None),
                                      SegmentHostMapping.host.is_(None))))

        results = query.all()

        # See if results are empty because the host isn't mapped to a segment
        if not results:
            # Check if it's a routed network (i.e subnets on segments)
            query = self._get_collection_query(context, Subnet)
            query = query.filter(Subnet.network_id == network_id)
            query = query.filter(Subnet.segment_id.isnot(None))
            if query.count() == 0:
                self._check_service_subnets(unfiltered_query, service_type)
                return []
            # It is a routed network but no subnets found for host
            raise segment_exc.HostNotConnectedToAnySegment(
                host=host, network_id=network_id)

        # For now, we're using a simplifying assumption that a host will only
        # touch one segment in a given routed network.  Raise exception
        # otherwise.  This restriction may be relaxed as use cases for multiple
        # mappings are understood.
        segment_ids = {subnet.segment_id
                       for subnet, mapping in results
                       if mapping}
        if 1 < len(segment_ids):
            raise segment_exc.HostConnectedToMultipleSegments(
                host=host, network_id=network_id)

        return self._sort_service_subnets(
                   [self._make_subnet_dict(subnet, context=context)
                    for subnet, _mapping in results],
                   unfiltered_query, service_type)

    def _make_subnet_args(self, detail, subnet, subnetpool_id):
        args = super(IpamBackendMixin, self)._make_subnet_args(
            detail, subnet, subnetpool_id)
        if validators.is_attr_set(subnet.get(segment.SEGMENT_ID)):
            args['segment_id'] = subnet[segment.SEGMENT_ID]
        if validators.is_attr_set(subnet.get('service_types')):
            args['service_types'] = subnet['service_types']
        return args

    def update_port(self, context, old_port_db, old_port, new_port):
        """Update the port IPs

        Updates the port's IPs based on any new fixed_ips passed in or if
        deferred IP allocation is in effect because allocation requires host
        binding information that wasn't provided until port update.

        :param old_port_db: The port database record
        :param old_port: A port dict created by calling _make_port_dict.  This
                         must be called before calling this method in order to
                         load data from extensions, specifically host binding.
        :param new_port: The new port data passed through the API.
        """
        old_host = old_port.get(portbindings.HOST_ID)
        new_host = new_port.get(portbindings.HOST_ID)
        host = new_host if validators.is_attr_set(new_host) else old_host

        changes = self.update_port_with_ips(context,
                                            host,
                                            old_port_db,
                                            new_port,
                                            new_port.get('mac_address'))

        fixed_ips_requested = validators.is_attr_set(new_port.get('fixed_ips'))
        old_ips = old_port.get('fixed_ips')
        deferred_ip_allocation = (host and not old_host
                                  and not old_ips
                                  and not fixed_ips_requested)
        if not deferred_ip_allocation:
            # Check that any existing IPs are valid on the new segment
            new_host_requested = host and host != old_host
            if old_ips and new_host_requested and not fixed_ips_requested:
                valid_subnets = self._ipam_get_subnets(
                    context, old_port['network_id'], host)
                valid_subnet_ids = {s['id'] for s in valid_subnets}
                for fixed_ip in old_ips:
                    if fixed_ip['subnet_id'] not in valid_subnet_ids:
                        raise segment_exc.HostNotCompatibleWithFixedIps(
                            host=host, port_id=old_port['id'])
            return changes

        # Allocate as if this were the port create.
        port_copy = copy.deepcopy(old_port)
        port_copy['fixed_ips'] = const.ATTR_NOT_SPECIFIED
        port_copy.update(new_port)
        context.session.expire(old_port_db, ['fixed_ips'])
        ips = self.allocate_ips_for_port_and_store(
            context, {'port': port_copy}, port_copy['id'])
        return self.Changes(add=ips, original=[], remove=[])
