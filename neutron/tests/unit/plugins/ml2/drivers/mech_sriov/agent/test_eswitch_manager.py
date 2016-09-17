# Copyright 2014 Mellanox Technologies, Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import os

import mock

from neutron.agent.linux import ip_link_support
from neutron.plugins.ml2.drivers.mech_sriov.agent.common \
    import exceptions as exc
from neutron.plugins.ml2.drivers.mech_sriov.agent import eswitch_manager as esm
from neutron.tests import base


class TestCreateESwitchManager(base.BaseTestCase):
    SCANNED_DEVICES = [('0000:06:00.1', 0),
                       ('0000:06:00.2', 1),
                       ('0000:06:00.3', 2)]

    @staticmethod
    def cleanup():
        if hasattr(esm.ESwitchManager, '_instance'):
            del esm.ESwitchManager._instance

    def test_create_eswitch_mgr_fail(self):
        device_mappings = {'physnet1': ['p6p1']}
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.PciOsWrapper.scan_vf_devices",
                        side_effect=exc.InvalidDeviceError(
                            dev_name="p6p1", reason="device" " not found")),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.PciOsWrapper.is_assigned_vf",
                           return_value=True):
            eswitch_mgr = esm.ESwitchManager()
            self.addCleanup(self.cleanup)
            self.assertRaises(exc.InvalidDeviceError,
                              eswitch_mgr.discover_devices,
                              device_mappings, None)

    def test_create_eswitch_mgr_ok(self):
        device_mappings = {'physnet1': ['p6p1']}
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.PciOsWrapper.scan_vf_devices",
                        return_value=self.SCANNED_DEVICES),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.PciOsWrapper.is_assigned_vf",
                           return_value=True):
            eswitch_mgr = esm.ESwitchManager()
            self.addCleanup(self.cleanup)
            eswitch_mgr.discover_devices(device_mappings, None)


class TestESwitchManagerApi(base.BaseTestCase):
    SCANNED_DEVICES = [('0000:06:00.1', 0),
                       ('0000:06:00.2', 1),
                       ('0000:06:00.3', 2)]

    ASSIGNED_MAC = '00:00:00:00:00:66'
    PCI_SLOT = '0000:06:00.1'
    WRONG_MAC = '00:00:00:00:00:67'
    WRONG_PCI = "0000:06:00.6"
    MAX_RATE = ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE
    MIN_RATE = ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_MIN_TX_RATE

    def setUp(self):
        super(TestESwitchManagerApi, self).setUp()
        device_mappings = {'physnet1': ['p6p1']}
        self.eswitch_mgr = esm.ESwitchManager()
        self.addCleanup(self.cleanup)
        self._set_eswitch_manager(self.eswitch_mgr, device_mappings)

    @staticmethod
    def cleanup():
        if hasattr(esm.ESwitchManager, '_instance'):
            del esm.ESwitchManager._instance

    def _set_eswitch_manager(self, eswitch_mgr, device_mappings):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.PciOsWrapper.scan_vf_devices",
                        return_value=self.SCANNED_DEVICES), \
                 mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                            "eswitch_manager.PciOsWrapper.is_assigned_vf",
                            return_value=True):
            eswitch_mgr.discover_devices(device_mappings, None)

    def test_get_assigned_devices_info(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_assigned_devices_info",
                        return_value=[(self.ASSIGNED_MAC, self.PCI_SLOT)]):
            result = self.eswitch_mgr.get_assigned_devices_info()
            self.assertIn(self.ASSIGNED_MAC, list(result)[0])
            self.assertIn(self.PCI_SLOT, list(result)[0])

    def test_get_assigned_devices_info_multiple_nics_for_physnet(self):
        device_mappings = {'physnet1': ['p6p1', 'p6p2']}
        devices_info = {
            'p6p1': [(self.ASSIGNED_MAC, self.PCI_SLOT)],
            'p6p2': [(self.WRONG_MAC, self.WRONG_PCI)],
        }

        def get_assigned_devices_info(self):
            return devices_info[self.dev_name]

        self._set_eswitch_manager(self.eswitch_mgr, device_mappings)

        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_assigned_devices_info",
                        side_effect=get_assigned_devices_info,
                        autospec=True):
            result = self.eswitch_mgr.get_assigned_devices_info()
            self.assertIn(devices_info['p6p1'][0], list(result))
            self.assertIn(devices_info['p6p2'][0], list(result))

    def test_get_device_status_true(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.EmbSwitch.get_device_state",
                           return_value=True):
            result = self.eswitch_mgr.get_device_state(self.ASSIGNED_MAC,
                                                       self.PCI_SLOT)
            self.assertTrue(result)

    def test_get_device_status_false(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.EmbSwitch.get_device_state",
                           return_value=False):
            result = self.eswitch_mgr.get_device_state(self.ASSIGNED_MAC,
                                                       self.PCI_SLOT)
            self.assertFalse(result)

    def test_get_device_status_mismatch(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.EmbSwitch.get_device_state",
                           return_value=True):
            with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                            "eswitch_manager.LOG.warning") as log_mock:
                result = self.eswitch_mgr.get_device_state(self.WRONG_MAC,
                                                           self.PCI_SLOT)
                log_mock.assert_called_with('device pci mismatch: '
                                            '%(device_mac)s - %(pci_slot)s',
                                            {'pci_slot': self.PCI_SLOT,
                                             'device_mac': self.WRONG_MAC})
                self.assertFalse(result)

    def test_set_device_status(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.EmbSwitch.set_device_state"):
            self.eswitch_mgr.set_device_state(self.ASSIGNED_MAC,
                                              self.PCI_SLOT, True)

    def test_set_device_max_rate(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC) as get_pci_mock,\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.EmbSwitch.set_device_rate")\
                as set_device_rate_mock:
            self.eswitch_mgr.set_device_max_rate(self.ASSIGNED_MAC,
                                                 self.PCI_SLOT, 1000)
            get_pci_mock.assert_called_once_with(self.PCI_SLOT)
            set_device_rate_mock.assert_called_once_with(
                self.PCI_SLOT, self.MAX_RATE, 1000)

    def test_set_device_min_tx_rate(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC) as get_pci_mock,\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.EmbSwitch.set_device_rate")\
                as set_device_rate_mock:
            self.eswitch_mgr.set_device_min_tx_rate(self.ASSIGNED_MAC,
                                                    self.PCI_SLOT, 1000)
            get_pci_mock.assert_called_once_with(self.PCI_SLOT)
            set_device_rate_mock.assert_called_once_with(
                self.PCI_SLOT, self.MIN_RATE, 1000)

    def test_set_device_status_mismatch(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.EmbSwitch.set_device_state"):
            with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                            "eswitch_manager.LOG.warning") as log_mock:
                self.eswitch_mgr.set_device_state(self.WRONG_MAC,
                                                  self.PCI_SLOT, True)
                log_mock.assert_called_with('device pci mismatch: '
                                            '%(device_mac)s - %(pci_slot)s',
                                            {'pci_slot': self.PCI_SLOT,
                                             'device_mac': self.WRONG_MAC})

    def _mock_device_exists(self, pci_slot, mac_address, expected_result):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC):
            result = self.eswitch_mgr.device_exists(mac_address,
                                                    pci_slot)
            self.assertEqual(expected_result, result)

    def test_device_exists_true(self):
        self._mock_device_exists(self.PCI_SLOT,
                                 self.ASSIGNED_MAC,
                                 True)

    def test_device_exists_false(self):
        self._mock_device_exists(self.WRONG_PCI,
                                 self.WRONG_MAC,
                                 False)

    def test_device_exists_mismatch(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.EmbSwitch.get_pci_device",
                        return_value=self.ASSIGNED_MAC):
            with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                            "eswitch_manager.LOG.warning") as log_mock:
                result = self.eswitch_mgr.device_exists(self.WRONG_MAC,
                                                        self.PCI_SLOT)
                log_mock.assert_called_with('device pci mismatch: '
                                            '%(device_mac)s - %(pci_slot)s',
                                            {'pci_slot': self.PCI_SLOT,
                                             'device_mac': self.WRONG_MAC})
                self.assertFalse(result)

    def _test_clear_rate(self, rate_type, pci_slot, passed, mac_address):
        with mock.patch('neutron.plugins.ml2.drivers.mech_sriov.agent.'
                        'eswitch_manager.EmbSwitch.set_device_rate') \
                as set_rate_mock, \
                mock.patch('neutron.plugins.ml2.drivers.mech_sriov.agent.'
                           'pci_lib.PciDeviceIPWrapper.get_assigned_macs',
                           return_value=mac_address):
            self.eswitch_mgr.clear_rate(pci_slot, rate_type)
            if passed:
                set_rate_mock.assert_called_once_with(pci_slot, rate_type, 0)
            else:
                self.assertFalse(set_rate_mock.called)

    def test_clear_rate_max_rate_existing_pci_slot(self):
        self._test_clear_rate(self.MAX_RATE, self.PCI_SLOT, passed=True,
                              mac_address={})

    def test_clear_rate_max_rate_exist_and_assigned_pci(self):
        self._test_clear_rate(self.MAX_RATE, self.PCI_SLOT, passed=False,
                              mac_address={0: self.ASSIGNED_MAC})

    def test_clear_rate_max_rate_nonexisting_pci_slot(self):
        self._test_clear_rate(self.MAX_RATE, self.WRONG_PCI, passed=False,
                              mac_address={})

    def test_clear_rate_min_tx_rate_existing_pci_slot(self):
        self._test_clear_rate(self.MIN_RATE, self.PCI_SLOT, passed=True,
                              mac_address={})

    def test_clear_rate_min_tx_rate_exist_and_assigned_pci(self):
        self._test_clear_rate(self.MIN_RATE, self.PCI_SLOT, passed=False,
                              mac_address={0: self.ASSIGNED_MAC})

    def test_clear_rate_min_tx_rate_nonexisting_pci_slot(self):
        self._test_clear_rate(self.MIN_RATE, self.WRONG_PCI, passed=False,
                              mac_address={})


class TestEmbSwitch(base.BaseTestCase):
    DEV_NAME = "eth2"
    PHYS_NET = "default"
    ASSIGNED_MAC = '00:00:00:00:00:66'
    PCI_SLOT = "0000:06:00.1"
    WRONG_PCI_SLOT = "0000:06:00.4"
    SCANNED_DEVICES = [('0000:06:00.1', 0),
                       ('0000:06:00.2', 1),
                       ('0000:06:00.3', 2)]
    VF_TO_MAC_MAPPING = {0: '00:00:00:00:00:11',
                         1: '00:00:00:00:00:22',
                         2: '00:00:00:00:00:33'}
    EXPECTED_MAC_TO_PCI = {
        '00:00:00:00:00:11': '0000:06:00.1',
        '00:00:00:00:00:22': '0000:06:00.2',
        '00:00:00:00:00:33': '0000:06:00.3'}

    def setUp(self):
        super(TestEmbSwitch, self).setUp()
        exclude_devices = set()
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.PciOsWrapper.scan_vf_devices",
                        return_value=self.SCANNED_DEVICES):
            self.emb_switch = esm.EmbSwitch(self.PHYS_NET, self.DEV_NAME,
                                            exclude_devices)

    @mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                "eswitch_manager.PciOsWrapper.scan_vf_devices",
                return_value=[(PCI_SLOT, 0)])
    def test_get_assigned_devices_info(self, *args):
        emb_switch = esm.EmbSwitch(self.PHYS_NET, self.DEV_NAME, ())
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.get_assigned_macs",
                        return_value={0: self.ASSIGNED_MAC}),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.PciOsWrapper.is_assigned_vf",
                           return_value=True):
            result = emb_switch.get_assigned_devices_info()
            self.assertIn(self.ASSIGNED_MAC, list(result)[0])
            self.assertIn(self.PCI_SLOT, list(result)[0])

    @mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                "eswitch_manager.PciOsWrapper.scan_vf_devices",
                return_value=SCANNED_DEVICES)
    def test_get_assigned_devices_info_multiple_slots(self, *args):
        emb_switch = esm.EmbSwitch(self.PHYS_NET, self.DEV_NAME, ())
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.get_assigned_macs",
                        return_value=self.VF_TO_MAC_MAPPING),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.PciOsWrapper.is_assigned_vf",
                           return_value=True):
            devices_info = emb_switch.get_assigned_devices_info()
            for device_info in devices_info:
                mac = device_info[0]
                pci_slot = device_info[1]
                self.assertEqual(
                    self.EXPECTED_MAC_TO_PCI[mac], pci_slot)

    def test_get_assigned_devices_empty(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                        "eswitch_manager.PciOsWrapper.is_assigned_vf",
                        return_value=False):
            result = self.emb_switch.get_assigned_devices_info()
            self.assertFalse(result)

    def test_get_device_state_ok(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.get_vf_state",
                        return_value=False):
            result = self.emb_switch.get_device_state(self.PCI_SLOT)
            self.assertFalse(result)

    def test_get_device_state_fail(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.get_vf_state",
                        return_value=False):
            self.assertRaises(exc.InvalidPciSlotError,
                              self.emb_switch.get_device_state,
                              self.WRONG_PCI_SLOT)

    def test_set_device_state_ok(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_state"):
            with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                            "pci_lib.LOG.warning") as log_mock:
                self.emb_switch.set_device_state(self.PCI_SLOT, True)
                self.assertEqual(0, log_mock.call_count)

    def test_set_device_state_fail(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_state"):
            self.assertRaises(exc.InvalidPciSlotError,
                              self.emb_switch.set_device_state,
                              self.WRONG_PCI_SLOT, True)

    def test_set_device_spoofcheck_ok(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_spoofcheck") as \
                                set_vf_spoofcheck_mock:
            self.emb_switch.set_device_spoofcheck(self.PCI_SLOT, True)
            self.assertTrue(set_vf_spoofcheck_mock.called)

    def test_set_device_spoofcheck_fail(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_spoofcheck"):
            self.assertRaises(exc.InvalidPciSlotError,
                              self.emb_switch.set_device_spoofcheck,
                              self.WRONG_PCI_SLOT, True)

    def test_set_device_rate_ok(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_rate") as pci_lib_mock:
            self.emb_switch.set_device_rate(
                self.PCI_SLOT,
                ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 2000)
            pci_lib_mock.assert_called_with(
                0, ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 2)

    def test_set_device_max_rate_ok2(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_rate") as pci_lib_mock:
            self.emb_switch.set_device_rate(
                self.PCI_SLOT,
                ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 99)
            pci_lib_mock.assert_called_with(
                0, ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 1)

    def test_set_device_max_rate_rounded_ok(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_rate") as pci_lib_mock:
            self.emb_switch.set_device_rate(
                self.PCI_SLOT,
                ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 2001)
            pci_lib_mock.assert_called_with(
                0, ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 2)

    def test_set_device_max_rate_rounded_ok2(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_rate") as pci_lib_mock:
            self.emb_switch.set_device_rate(
                self.PCI_SLOT,
                ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 2499)
            pci_lib_mock.assert_called_with(
                0, ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 2)

    def test_set_device_max_rate_rounded_ok3(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_rate") as pci_lib_mock:
            self.emb_switch.set_device_rate(
                self.PCI_SLOT,
                ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 2500)
            pci_lib_mock.assert_called_with(
                0, ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 3)

    def test_set_device_max_rate_disable(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_rate") as pci_lib_mock:
            self.emb_switch.set_device_rate(
                self.PCI_SLOT,
                ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 0)
            pci_lib_mock.assert_called_with(
                0, ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 0)

    def test_set_device_max_rate_fail(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.set_vf_rate"):
            self.assertRaises(
                exc.InvalidPciSlotError,
                self.emb_switch.set_device_rate,
                self.WRONG_PCI_SLOT,
                ip_link_support.IpLinkConstants.IP_LINK_CAPABILITY_RATE, 1000)

    def test_get_pci_device(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.get_assigned_macs",
                        return_value={0: self.ASSIGNED_MAC}),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.PciOsWrapper.is_assigned_vf",
                           return_value=True):
            result = self.emb_switch.get_pci_device(self.PCI_SLOT)
            self.assertEqual(self.ASSIGNED_MAC, result)

    def test_get_pci_device_fail(self):
        with mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                        "PciDeviceIPWrapper.get_assigned_macs",
                        return_value=[self.ASSIGNED_MAC]),\
                mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent."
                           "eswitch_manager.PciOsWrapper.is_assigned_vf",
                           return_value=True):
            result = self.emb_switch.get_pci_device(self.WRONG_PCI_SLOT)
            self.assertIsNone(result)

    def test_get_pci_list(self):
        result = self.emb_switch.get_pci_slot_list()
        self.assertEqual([tup[0] for tup in self.SCANNED_DEVICES],
                         sorted(result))


class TestPciOsWrapper(base.BaseTestCase):
    DEV_NAME = "p7p1"
    VF_INDEX = 1
    DIR_CONTENTS = [
        "mlx4_port1",
        "virtfn0",
        "virtfn1",
        "virtfn2"
    ]
    DIR_CONTENTS_NO_MATCH = [
        "mlx4_port1",
        "mlx4_port1"
    ]
    LINKS = {
        "virtfn0": "../0000:04:00.1",
        "virtfn1": "../0000:04:00.2",
        "virtfn2": "../0000:04:00.3"
    }
    PCI_SLOTS = [
        ('0000:04:00.1', 0),
        ('0000:04:00.2', 1),
        ('0000:04:00.3', 2)
    ]

    def test_scan_vf_devices(self):
        def _get_link(file_path):
            file_name = os.path.basename(file_path)
            return self.LINKS[file_name]

        with mock.patch("os.path.isdir", return_value=True),\
                mock.patch("os.listdir", return_value=self.DIR_CONTENTS),\
                mock.patch("os.path.islink", return_value=True),\
                mock.patch("os.readlink", side_effect=_get_link):
            result = esm.PciOsWrapper.scan_vf_devices(self.DEV_NAME)
            self.assertEqual(self.PCI_SLOTS, result)

    def test_scan_vf_devices_no_dir(self):
        with mock.patch("os.path.isdir", return_value=False):
            self.assertRaises(exc.InvalidDeviceError,
                              esm.PciOsWrapper.scan_vf_devices,
                              self.DEV_NAME)

    def test_scan_vf_devices_no_content(self):
        with mock.patch("os.path.isdir", return_value=True),\
                mock.patch("os.listdir", return_value=[]):
            self.assertEqual([],
                             esm.PciOsWrapper.scan_vf_devices(self.DEV_NAME))

    def test_scan_vf_devices_no_match(self):
        with mock.patch("os.path.isdir", return_value=True),\
                mock.patch("os.listdir",
                           return_value=self.DIR_CONTENTS_NO_MATCH):
            self.assertEqual([],
                             esm.PciOsWrapper.scan_vf_devices(self.DEV_NAME))

    @mock.patch("os.listdir", side_effect=OSError())
    def test_is_assigned_vf_true(self, *args):
        self.assertTrue(esm.PciOsWrapper.is_assigned_vf(
            self.DEV_NAME, self.VF_INDEX))

    @mock.patch("os.listdir", return_value=[DEV_NAME, "eth1"])
    @mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                "PciDeviceIPWrapper.is_macvtap_assigned", return_value=False)
    def test_is_assigned_vf_false(self, *args):
        self.assertFalse(esm.PciOsWrapper.is_assigned_vf(
            self.DEV_NAME, self.VF_INDEX))

    @mock.patch("os.listdir", return_value=["eth0", "eth1"])
    @mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                "PciDeviceIPWrapper.is_macvtap_assigned", return_value=True)
    def test_is_assigned_vf_macvtap(
        self, mock_is_macvtap_assigned, *args):
        esm.PciOsWrapper.is_assigned_vf(self.DEV_NAME, self.VF_INDEX)
        mock_is_macvtap_assigned.called_with(self.VF_INDEX, "eth0")

    @mock.patch("os.listdir", side_effect=OSError())
    @mock.patch("neutron.plugins.ml2.drivers.mech_sriov.agent.pci_lib."
                "PciDeviceIPWrapper.is_macvtap_assigned")
    def test_is_assigned_vf_macvtap_failure(
        self, mock_is_macvtap_assigned, *args):
        esm.PciOsWrapper.is_assigned_vf(self.DEV_NAME, self.VF_INDEX)
        self.assertFalse(mock_is_macvtap_assigned.called)
