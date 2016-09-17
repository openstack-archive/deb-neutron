# Copyright (c) 2016 Mellanox Technologies, Ltd
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

import sys

from neutron_lib import constants
from oslo_config import cfg
from oslo_log import log as logging

from neutron._i18n import _, _LE, _LW
from neutron.agent.l2 import l2_agent_extension
from neutron.agent.linux import bridge_lib
from neutron.common import utils as n_utils
from neutron.plugins.ml2.drivers.linuxbridge.agent.common import (
     constants as linux_bridge_constants)
from neutron.plugins.ml2.drivers.openvswitch.agent.common import (
     constants as ovs_constants)

# if shared_physical_device_mappings is not configured KeyError will be thrown
fdb_population_opt = [
    cfg.ListOpt('shared_physical_device_mappings', default=[],
                help=_("Comma-separated list of "
                       "<physical_network>:<network_device> tuples mapping "
                       "physical network names to the agent's node-specific "
                       "shared physical network device between "
                       "SR-IOV and OVS or SR-IOV and linux bridge"))
]
cfg.CONF.register_opts(fdb_population_opt, 'FDB')

LOG = logging.getLogger(__name__)


class FdbPopulationAgentExtension(
        l2_agent_extension.L2AgentExtension):
    """The FDB population is an agent extension to OVS or linux bridge
    who's objective is to update the FDB table for existing instance
    using normal port, thus enabling communication between SR-IOV instances
    and normal instances.
    Additional information describing the problem can be found here:
    http://events.linuxfoundation.org/sites/events/files/slides/LinuxConJapan2014_makita_0.pdf
    """

    # FDB udpates are triggered for ports with a certain device_owner only:
    # - device owner "compute": updates the FDB with normal port instances,
    #       required in order to enable communication between
    #       SR-IOV direct port instances and normal port instance.
    # - device owner "router_interface": updates the FDB with OVS/LB ports,
    #       required in order to enable communication for SR-IOV instances
    #       with floating ip that are located with the network node.
    # - device owner "DHCP": updates the FDB with the dhcp server.
    #       When the lease expires a unicast renew message is sent
    #       to the dhcp server. In case the FDB is not updated
    #       the message will be sent to the wire, causing the message
    #       to get lost in case the sender uses direct port and is
    #       located on the same hypervisor as the network node.
    PERMITTED_DEVICE_OWNERS = {constants.DEVICE_OWNER_COMPUTE_PREFIX,
                               constants.DEVICE_OWNER_ROUTER_INTF,
                               constants.DEVICE_OWNER_DHCP}

    class FdbTableTracker(object):
        """FDB table tracker is a helper class
        intended to keep track of the existing FDB rules.
        """
        def __init__(self, devices):
            self.device_to_macs = {}
            self.portid_to_mac = {}
            # update macs already in the physical interface's FDB table
            for device in devices:
                try:
                    _stdout = bridge_lib.FdbInterface.show(device)
                except RuntimeError as e:
                    LOG.warning(_LW(
                        'Unable to find FDB Interface %(device)s. '
                        'Exception: %(e)s'), {'device': device, 'e': e})
                    continue
                self.device_to_macs[device] = _stdout.split()[::3]

        def update_port(self, device, port_id, mac):
            # check if device is updated
            if self.device_to_macs.get(device) == mac:
                return
            # delete invalid port_id's mac from the FDB,
            # in case the port was updated to another mac
            self.delete_port([device], port_id)
            # update port id
            self.portid_to_mac[port_id] = mac
            # check if rule for mac already exists
            if mac in self.device_to_macs[device]:
                return
            try:
                bridge_lib.FdbInterface.add(mac, device)
            except RuntimeError as e:
                LOG.warning(_LW(
                    'Unable to add mac %(mac)s '
                    'to FDB Interface %(device)s. '
                    'Exception: %(e)s'),
                    {'mac': mac, 'device': device, 'e': e})
                return
            self.device_to_macs[device].append(mac)

        def delete_port(self, devices, port_id):
            mac = self.portid_to_mac.get(port_id)
            if mac is None:
                LOG.warning(_LW('Port Id %(port_id)s does not have a rule for '
                    'devices %(devices)s in FDB table'),
                    {'port_id': port_id, 'devices': devices})
                return
            for device in devices:
                if mac in self.device_to_macs[device]:
                    try:
                        bridge_lib.FdbInterface.delete(mac, device)
                    except RuntimeError as e:
                        LOG.warning(_LW(
                            'Unable to delete mac %(mac)s '
                            'from FDB Interface %(device)s. '
                            'Exception: %(e)s'),
                            {'mac': mac, 'device': device, 'e': e})
                        return
                    self.device_to_macs[device].remove(mac)
                    del self.portid_to_mac[port_id]

    # class FdbPopulationAgentExtension implementation:
    def initialize(self, connection, driver_type):
        """Perform FDB Agent Extension initialization."""
        valid_driver_types = (linux_bridge_constants.EXTENSION_DRIVER_TYPE,
                              ovs_constants.EXTENSION_DRIVER_TYPE)
        if driver_type not in valid_driver_types:
            LOG.error(_LE('FDB extension is only supported for OVS and '
                          'linux bridge agent, currently uses '
                          '%(driver_type)s'), {'driver_type': driver_type})
            sys.exit(1)

        self.device_mappings = n_utils.parse_mappings(
            cfg.CONF.FDB.shared_physical_device_mappings, unique_keys=False)
        devices = self._get_devices()
        if not devices:
            LOG.error(_LE('Invalid configuration provided for FDB extension: '
                          'no physical devices'))
            sys.exit(1)
        self.fdb_tracker = self.FdbTableTracker(devices)

    def handle_port(self, context, details):
        """Handle agent FDB population extension for port."""
        device_owner = details['device_owner']
        if self._is_valid_device_owner(device_owner):
            mac = details['mac_address']
            port_id = details['port_id']
            physnet = details.get('physical_network')
            if physnet and physnet in self.device_mappings:
                for device in self.device_mappings[physnet]:
                    self.fdb_tracker.update_port(device, port_id, mac)

    def delete_port(self, context, details):
        """Delete port from FDB population extension."""
        port_id = details['port_id']
        devices = self._get_devices()
        self.fdb_tracker.delete_port(devices, port_id)

    def _get_devices(self):
        def _flatten_list(l):
            return [item for sublist in l for item in sublist]

        return _flatten_list(self.device_mappings.values())

    def _is_valid_device_owner(self, device_owner):
        for permitted_device_owner in self.PERMITTED_DEVICE_OWNERS:
            if device_owner.startswith(permitted_device_owner):
                return True
        return False
