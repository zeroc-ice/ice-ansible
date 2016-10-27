#!/usr/bin/env python
# -*- coding: utf-8 -*-
# **********************************************************************
#
# Copyright (c) 2016 ZeroC, Inc. All rights reserved.
#
# **********************************************************************

DOCUMENTATION = '''
---
module: icegrid_servers
short_description: Control servers running on an IceGrid Registry
description:
    - Start, stop, enable, and disable servers controlled by an IceGrid Registry. Ice for Python is required.
options:
    locator:
        required: false
        description:
            - IceGrid locator. (Required if config file does not specify Ice.Default.Locator)
    config:
        required: false
        description:
            - Ice Configuration file. (Must contain Ice.Default.Locator if locator is not set)
    servers:
        required: false
        description:
            - List of servers running on IceGrid. All servers running on registry will be modified if not set.
    state:
        required: false
        choices: [ started, stopped ]
        description:
            - Start or stop servers if necessary.
    enabled:
        required: false
        choices: [ "yes", "no" ]
        description:
            - Whether the servers should be 'enabled' or 'disabled' in IceGrid. At lest one
              of state and enabled are required.
    username:
        required: false
        description:
            - Username used to authenticate with IceGrid Registry. Only required if not using secure.
    password:
        required: false
        description:
            - Password used to authenticate with IceGrid Registry. Only required if not using secure.
    secure:
        required: false
        default: "no"
        description:
            - Connect to IceGrid using SSL/TLS based client authentication.
    skip:
        required: false
        default: "no"
        choices: [ "yes", "no" ]
        description:
            - Skip servers which are not listed in the IceGrid Registry. If set to 'no', an error will be occur if
              a specified server does not exist.
    args:
        required: false
        description:
            - List of arguments to pass for Ice configuration.
'''

EXAMPLES = '''
# Example action to start all servers, if not running
- icegrid_servers: locator="DemoIceGrid/Locator:default -h localhost -p 4061" state=started

# Example action to enable all servers, if not enabled, using configuration file
- icegrid_servers: config=config.grid enabled=yes
'''

RETURN = '''
servers:
    description: List of servers
    returned: success
    type: list
    sample: ['server1', 'server2']
enabled:
    description: List of servers which were enabled. Servers which were already enabled will not be included.
    returned: when enabled == 'yes'
    type: list
    sample: ['server1']
disabled:
    description: List of servers which were disabled. Servers which were already disabled will not be included.
    returned: when enabled == 'no'
    type: list
    sample: ['server2']
stateChanged:
    description: List of servers whose state was updated.
    returned: when state is not None
    type: list
    sample: ['server1', 'server2']
...
'''

from ansible.module_utils.basic import *

import Ice, IceGrid
from IceGrid import ServerState

class IceGridModule(Ice.Application):

    def __init__(self, module):
        Ice.Application.__init__(self)
        self.module         = module
        self.locator        = module.params['locator']
        self.servers        = module.params['servers']
        self.state          = module.params['state']
        self.enabled        = module.params['enabled']
        self.username       = module.params['username']
        self.password       = module.params['password']
        self.secure         = module.params['secure']
        self.skip           = module.params['skip']
        self.serverState    = {}
        self.result         = {}

    def run(self, args):
        comm = self.communicator()

        if self.locator is not None:
            try:
                locatorPrx = Ice.LocatorPrx.uncheckedCast(comm.stringToProxy(self.locator))
                comm.setDefaultLocator(locatorPrx)
            except Ice.IdentityParseException as ex:
                self.module.fail_json(msg="Failed to parse locator. {}".format(ex))

        try:
            category = comm.getDefaultLocator().ice_getIdentity().category
            registry = IceGrid.RegistryPrx.checkedCast(comm.stringToProxy(category + "/Registry"))
        except Ice.LocalException as ex:
            self.module.fail_json(msg="Error connecting to IceGrid Registry. {}".format(ex))

        try:
            if self.secure:
                session = registry.createAdminSessionFromSecureConnection()
            else:
                session = registry.createAdminSession(self.username, self.password)
        except IceGrid.PermissionDeniedException:
            self.module.fail_json(msg="Permission denied. Please verify username and password.")

        self.admin = session.getAdmin()

        self.allServers = self.admin.getAllServerIds()

        if self.servers is None:
            self.servers = self.allServers
        elif not self.skip:
            nonExistantServers = [m for m in self.servers if m not in self.allServers]
            if not nonExistantServers:
                self.module.fail_json(msg="The following servers do not exist: {}".format(', '.join(nonExistantServers)))
        else:
            self.servers = [m for m in self.servers if m in self.allServers]

        # The list of servers we're actually acting on.
        self.result['servers'] = self.servers

        # If we don't have any servers, just return now.
        if not self.servers:
            self.module.exit_json(**self.result)

        responses = []
        for server in self.servers:
            isEnabled = self.admin.begin_isServerEnabled(server)
            serverState = self.admin.begin_getServerState(server)
            responses.append((server, isEnabled, serverState))

        for response in responses:
            # A list of tuples ("server id", "server enabled", "server state"). This is later used to determine
            # if we really need to perform an action on a server.
            self.serverState[response[0]] = (self.admin.end_isServerEnabled(response[1]),
                                             self.admin.end_getServerState(response[2]))

        self.result['changed'] = False
        if self.enabled is not None:
            self.result['enabled' if self.enabled == True else 'disabled'] = []
            self.enableServers()

        if self.state is not None:
            self.result['stateChanged'] = []
            self.updateServerState()

        self.module.exit_json(**self.result)

    # Enable or disable servers based on "enabled" setting.
    def enableServers(self):
        responses = []
        for server in self.servers:
            if self.serverState[server][0] != self.enabled:
                responses.append((server, self.admin.begin_enableServer(server, self.enabled)))

        for server, r in responses:
            try:
                self.admin.end_enableServer(r)
                self.result['enabled' if self.enabled else 'disabled'].append(server)
            except IceGrid.ServerNotExistException as ex:
                self.module.fail_json(msg="Server {} does not exist".format(ex.id))
            except IceGrid.NodeUnreachableException as ex:
                self.module.fail_json(msg="Node {} could not be reached. {}".format(ex.name, ex.reason))
            except IceGrid.DeploymentException as ex:
                self.module.fail_json(msg="IceGrid.DeploymentException: {}".format(ex.reason))

        # We know for sure that some sever was enabled or disabled if we have at least one response
        if responses:
            self.result['changed'] = True

    # Update the state of servers to either be started or stopped
    def updateServerState(self):
        unchangedStateList = []
        if self.state == 'started':
            unchangedStateList.extend([ServerState.Active, ServerState.Activating])
            beginCall = lambda s: self.admin.begin_startServer(s)
            endCall =  lambda r: self.admin.end_startServer(r)
        elif self.state == 'stopped':
            unchangedStateList.extend([ServerState.Inactive, ServerState.Deactivating, ServerState.Destroying])
            beginCall = lambda s: self.admin.begin_stopServer(s)
            endCall =  lambda r: self.admin.end_stopServer(r)
        else:
            # It should be impossible to get this far.
            self.module.fail_json(msg="Unknown state {}.".format(self.state))

        responses = []
        for server in self.servers:
            if self.serverState[server][1] not in unchangedStateList:
                responses.append((server, beginCall(server)))

        for server, r in responses:
            try:
                endCall(r)
                self.result['stateChanged'].append(server)
            except IceGrid.ServerNotExistException as ex:
                self.module.fail_json(msg="Server {} does not exist".format(ex.id))
            except IceGrid.ServerStartException as ex:
                self.module.fail_json(msg="Failed to start server {}. {}".format(ex.id, ex.reason))
            except IceGrid.ServerStopException as ex:
                self.module.fail_json(msg="Failed to stop server {}. {}".format(ex.id, ex.reason))
            except IceGrid.NodeUnreachableException as ex:
                self.module.fail_json(msg="Node {} could not be reached. {}".format(ex.name, ex.reason))
            except IceGrid.DeploymentException as ex:
                self.module.fail_json(msg="IceGrid.DeploymentException: {}".format(ex.reason))

        # We know for sure that some state was changed if we have at least one response
        if responses:
            self.result['changed'] = True

def main():
    argument_spec = dict(
        locator=dict(required=False, default=None, type='str'),
        config=dict(required=False, default=None, type='path'),
        servers=dict(required=False, default=None, type='list'),
        state=dict(required=False, choices=['started', 'stopped'], default=None, type='str'),
        enabled=dict(required=False, default=None, type='bool'),
        username=dict(required=False, default=None, type='str'),
        password=dict(required=False, default=None, type='str'),
        secure=dict(required=False, default='no', type='bool'),
        skip=dict(required=False, default='no', type='bool'),
        args=dict(required=False, default=[], type='list')
    )

    module = AnsibleModule(argument_spec = argument_spec)

    if module.params['locator'] is None and module.params['config'] is None:
        module.fail_json(msg="One of 'locator' or 'config' must be set.")

    if module.params['state'] is None and module.params['enabled'] is None:
        module.fail_json(msg="One of 'state' or 'enabled' must be set.")

    if not module.params['secure'] and module.params['username'] is None or module.params['password'] is None:
        module.fail_json(msg="Username and password must be set when using password based client authentication. " +
                             "Otherwise set 'secure=yes' to use SSL/TLS based client authentication.")

    args = ['Ansible IceGrid Server Module']
    args.extend(module.params['args'])

    app = IceGridModule(module)
    app.main(args, module.params['config'])

if __name__ == '__main__':
    main()
