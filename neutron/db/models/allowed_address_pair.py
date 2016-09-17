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

from neutron_lib.db import model_base
import sqlalchemy as sa
from sqlalchemy import orm

from neutron.db import models_v2


class AllowedAddressPair(model_base.BASEV2):
    port_id = sa.Column(sa.String(36),
                        sa.ForeignKey('ports.id', ondelete="CASCADE"),
                        primary_key=True)
    mac_address = sa.Column(sa.String(32), nullable=False, primary_key=True)
    ip_address = sa.Column(sa.String(64), nullable=False, primary_key=True)

    port = orm.relationship(
        models_v2.Port,
        backref=orm.backref("allowed_address_pairs",
                            lazy="joined", cascade="delete"))
