# Copyright 2012 VMware, Inc.  All rights reserved.
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
from sqlalchemy import orm

from neutron.api.v2 import attributes
from neutron.db import models_v2
from neutron.db import standard_attr


class SecurityGroup(standard_attr.HasStandardAttributes, model_base.BASEV2,
                    model_base.HasId, model_base.HasProject):
    """Represents a v2 neutron security group."""

    name = sa.Column(sa.String(attributes.NAME_MAX_LEN))


class DefaultSecurityGroup(model_base.BASEV2, model_base.HasProjectPrimaryKey):
    __tablename__ = 'default_security_group'

    security_group_id = sa.Column(sa.String(36),
                                  sa.ForeignKey("securitygroups.id",
                                                ondelete="CASCADE"),
                                  nullable=False)
    security_group = orm.relationship(
        SecurityGroup, lazy='joined',
        backref=orm.backref('default_security_group', cascade='all,delete'),
        primaryjoin="SecurityGroup.id==DefaultSecurityGroup.security_group_id",
    )


class SecurityGroupPortBinding(model_base.BASEV2):
    """Represents binding between neutron ports and security profiles."""

    port_id = sa.Column(sa.String(36),
                        sa.ForeignKey("ports.id",
                                      ondelete='CASCADE'),
                        primary_key=True)
    security_group_id = sa.Column(sa.String(36),
                                  sa.ForeignKey("securitygroups.id"),
                                  primary_key=True)
    revises_on_change = ('ports', )
    # Add a relationship to the Port model in order to instruct SQLAlchemy to
    # eagerly load security group bindings
    ports = orm.relationship(
        models_v2.Port,
        backref=orm.backref("security_groups",
                            lazy='joined', cascade='delete'))


class SecurityGroupRule(standard_attr.HasStandardAttributes, model_base.BASEV2,
                        model_base.HasId, model_base.HasProject):
    """Represents a v2 neutron security group rule."""

    security_group_id = sa.Column(sa.String(36),
                                  sa.ForeignKey("securitygroups.id",
                                                ondelete="CASCADE"),
                                  nullable=False)

    remote_group_id = sa.Column(sa.String(36),
                                sa.ForeignKey("securitygroups.id",
                                              ondelete="CASCADE"),
                                nullable=True)
    revises_on_change = ('security_group', )
    direction = sa.Column(sa.Enum('ingress', 'egress',
                                  name='securitygrouprules_direction'))
    ethertype = sa.Column(sa.String(40))
    protocol = sa.Column(sa.String(40))
    port_range_min = sa.Column(sa.Integer)
    port_range_max = sa.Column(sa.Integer)
    remote_ip_prefix = sa.Column(sa.String(255))
    security_group = orm.relationship(
        SecurityGroup,
        backref=orm.backref('rules', cascade='all,delete', lazy='joined'),
        primaryjoin="SecurityGroup.id==SecurityGroupRule.security_group_id")
    source_group = orm.relationship(
        SecurityGroup,
        backref=orm.backref('source_rules', cascade='all,delete'),
        primaryjoin="SecurityGroup.id==SecurityGroupRule.remote_group_id")
