# Copyright (c) 2015-2016 Hewlett Packard Enterprise Development Company LP
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

from neutron_lib.db import model_base
import sqlalchemy as sa


class AutoAllocatedTopology(model_base.BASEV2,
                            model_base.HasProjectPrimaryKey):

    __tablename__ = 'auto_allocated_topologies'

    network_id = sa.Column(sa.String(36),
                           sa.ForeignKey('networks.id',
                                         ondelete='CASCADE'),
                           nullable=False)
    router_id = sa.Column(sa.String(36),
                          sa.ForeignKey('routers.id',
                                        ondelete='SET NULL'),
                          nullable=True)
