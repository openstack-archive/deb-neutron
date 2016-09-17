# Copyright 2011 VMware, Inc.
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


from oslo_config import cfg
from oslo_service import wsgi

from neutron._i18n import _
from neutron.common import constants
from neutron.common import utils


core_opts = [
    cfg.StrOpt('bind_host', default='0.0.0.0',
               help=_("The host IP to bind to")),
    cfg.PortOpt('bind_port', default=9696,
                help=_("The port to bind to")),
    cfg.StrOpt('api_extensions_path', default="",
               help=_("The path for API extensions. "
                      "Note that this can be a colon-separated list of paths. "
                      "For example: api_extensions_path = "
                      "extensions:/path/to/more/exts:/even/more/exts. "
                      "The __path__ of neutron.extensions is appended to "
                      "this, so if your extensions are in there you don't "
                      "need to specify them here.")),
    cfg.StrOpt('auth_strategy', default='keystone',
               help=_("The type of authentication to use")),
    cfg.StrOpt('core_plugin',
               help=_("The core plugin Neutron will use")),
    cfg.ListOpt('service_plugins', default=[],
                help=_("The service plugins Neutron will use")),
    cfg.StrOpt('base_mac', default="fa:16:3e:00:00:00",
               help=_("The base MAC address Neutron will use for VIFs. "
                      "The first 3 octets will remain unchanged. If the 4th "
                      "octet is not 00, it will also be used. The others "
                      "will be randomly generated.")),
    cfg.IntOpt('mac_generation_retries', default=16,
               deprecated_for_removal=True,
               help=_("How many times Neutron will retry MAC generation. This "
                      "option is now obsolete and so is deprecated to be "
                      "removed in the Ocata release.")),
    cfg.BoolOpt('allow_bulk', default=True,
                help=_("Allow the usage of the bulk API")),
    cfg.BoolOpt('allow_pagination', default=True,
                deprecated_for_removal=True,
                help=_("Allow the usage of the pagination. This option has "
                       "been deprecated and will now be enabled "
                       "unconditionally.")),
    cfg.BoolOpt('allow_sorting', default=True,
                deprecated_for_removal=True,
                help=_("Allow the usage of the sorting. This option has been "
                       "deprecated and will now be enabled unconditionally.")),
    cfg.StrOpt('pagination_max_limit', default="-1",
               help=_("The maximum number of items returned in a single "
                      "response, value was 'infinite' or negative integer "
                      "means no limit")),
    cfg.ListOpt('default_availability_zones', default=[],
                help=_("Default value of availability zone hints. The "
                       "availability zone aware schedulers use this when "
                       "the resources availability_zone_hints is empty. "
                       "Multiple availability zones can be specified by a "
                       "comma separated string. This value can be empty. "
                       "In this case, even if availability_zone_hints for "
                       "a resource is empty, availability zone is "
                       "considered for high availability while scheduling "
                       "the resource.")),
    cfg.IntOpt('max_dns_nameservers', default=5,
               help=_("Maximum number of DNS nameservers per subnet")),
    cfg.IntOpt('max_subnet_host_routes', default=20,
               help=_("Maximum number of host routes per subnet")),
    cfg.IntOpt('max_fixed_ips_per_port', default=5,
               deprecated_for_removal=True,
               help=_("Maximum number of fixed ips per port. This option "
                      "is deprecated and will be removed in the N "
                      "release.")),
    cfg.BoolOpt('ipv6_pd_enabled', default=False,
                help=_("Enables IPv6 Prefix Delegation for automatic subnet "
                       "CIDR allocation. "
                       "Set to True to enable IPv6 Prefix Delegation for "
                       "subnet allocation in a PD-capable environment. Users "
                       "making subnet creation requests for IPv6 subnets "
                       "without providing a CIDR or subnetpool ID will be "
                       "given a CIDR via the Prefix Delegation mechanism. "
                       "Note that enabling PD will override the behavior of "
                       "the default IPv6 subnetpool.")),
    cfg.IntOpt('dhcp_lease_duration', default=86400,
               deprecated_name='dhcp_lease_time',
               help=_("DHCP lease duration (in seconds). Use -1 to tell "
                      "dnsmasq to use infinite lease times.")),
    cfg.StrOpt('dns_domain',
               default='openstacklocal',
               help=_('Domain to use for building the hostnames')),
    cfg.StrOpt('external_dns_driver',
               help=_('Driver for external DNS integration.')),
    cfg.BoolOpt('dhcp_agent_notification', default=True,
                help=_("Allow sending resource operation"
                       " notification to DHCP agent")),
    cfg.BoolOpt('allow_overlapping_ips', default=False,
                help=_("Allow overlapping IP support in Neutron. "
                       "Attention: the following parameter MUST be set to "
                       "False if Neutron is being used in conjunction with "
                       "Nova security groups.")),
    cfg.StrOpt('host', default=utils.get_hostname(),
               sample_default='example.domain',
               help=_("Hostname to be used by the Neutron server, agents and "
                      "services running on this machine. All the agents and "
                      "services running on this machine must use the same "
                      "host value.")),
    cfg.BoolOpt('notify_nova_on_port_status_changes', default=True,
                help=_("Send notification to nova when port status changes")),
    cfg.BoolOpt('notify_nova_on_port_data_changes', default=True,
                help=_("Send notification to nova when port data (fixed_ips/"
                       "floatingip) changes so nova can update its cache.")),
    cfg.IntOpt('send_events_interval', default=2,
               help=_('Number of seconds between sending events to nova if '
                      'there are any events to send.')),
    cfg.BoolOpt('advertise_mtu', default=True,
                deprecated_for_removal=True,
                help=_('If True, advertise network MTU values if core plugin '
                       'calculates them. MTU is advertised to running '
                       'instances via DHCP and RA MTU options.')),
    cfg.StrOpt('ipam_driver', default='internal',
               help=_("Neutron IPAM (IP address management) driver to use. "
                      "By default, the reference implementation of the "
                      "Neutron IPAM driver is used.")),
    cfg.BoolOpt('vlan_transparent', default=False,
                help=_('If True, then allow plugins that support it to '
                       'create VLAN transparent networks.')),
    cfg.StrOpt('web_framework', default='legacy',
               choices=('legacy', 'pecan'),
               help=_("This will choose the web framework in which to run "
                      "the Neutron API server. 'pecan' is a new experimental "
                      "rewrite of the API server.")),
    cfg.IntOpt('global_physnet_mtu', default=constants.DEFAULT_NETWORK_MTU,
               deprecated_name='segment_mtu', deprecated_group='ml2',
               help=_('MTU of the underlying physical network. Neutron uses '
                      'this value to calculate MTU for all virtual network '
                      'components. For flat and VLAN networks, neutron uses '
                      'this value without modification. For overlay networks '
                      'such as VXLAN, neutron automatically subtracts the '
                      'overlay protocol overhead from this value. Defaults '
                      'to 1500, the standard value for Ethernet.'))
]

core_cli_opts = [
    cfg.StrOpt('state_path',
               default='/var/lib/neutron',
               help=_("Where to store Neutron state files. "
                      "This directory must be writable by the agent.")),
]


def register_core_common_config_opts(cfg=cfg.CONF):
    cfg.register_opts(core_opts)
    cfg.register_cli_opts(core_cli_opts)
    wsgi.register_opts(cfg)


NOVA_CONF_SECTION = 'nova'

nova_opts = [
    cfg.StrOpt('region_name',
               help=_('Name of nova region to use. Useful if keystone manages'
                      ' more than one region.')),
    cfg.StrOpt('endpoint_type',
               default='public',
               choices=['public', 'admin', 'internal'],
               help=_('Type of the nova endpoint to use.  This endpoint will'
                      ' be looked up in the keystone catalog and should be'
                      ' one of public, internal or admin.')),
]


def register_nova_opts(cfg=cfg.CONF):
    cfg.register_opts(nova_opts, group=NOVA_CONF_SECTION)
