..
      Licensed under the Apache License, Version 2.0 (the "License"); you may
      not use this file except in compliance with the License. You may obtain
      a copy of the License at

          http://www.apache.org/licenses/LICENSE-2.0

      Unless required by applicable law or agreed to in writing, software
      distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
      WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
      License for the specific language governing permissions and limitations
      under the License.


      Convention for heading levels in Neutron devref:
      =======  Heading 0 (reserved for the title in a document)
      -------  Heading 1
      ~~~~~~~  Heading 2
      +++++++  Heading 3
      '''''''  Heading 4
      (Avoid deeper levels because they do not render well.)


L2 agent extensions
===================

L2 agent extensions are part of a generalized L2/L3 extension framework. See
:doc:`agent extensions <agent_extensions>`.

Open vSwitch agent API
~~~~~~~~~~~~~~~~~~~~~~

* neutron.plugins.ml2.drivers.openvswitch.agent.ovs_agent_extension_api

Open vSwitch agent API object includes two methods that return wrapped and
hardened bridge objects with cookie values allocated for calling extensions::

#. request_int_br
#. request_tun_br

Bridge objects returned by those methods already have new default cookie values
allocated for extension flows. All flow management methods (add_flow, mod_flow,
...) enforce those allocated cookies.
