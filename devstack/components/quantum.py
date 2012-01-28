# vim: tabstop=4 shiftwidth=4 softtabstop=4

#    Copyright (C) 2012 Yahoo! Inc. All Rights Reserved.
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

import io

from devstack import cfg
from devstack import component as comp
from devstack import log as logging
from devstack import settings
from devstack import shell as sh
from devstack import utils
from devstack.components import db

LOG = logging.getLogger("devstack.components.quantum")

#vswitch pkgs
VSWITCH_PLUGIN = 'openvswitch'
PKG_VSWITCH = "quantum-openvswitch.json"
V_PROVIDER = "quantum.plugins.openvswitch.ovs_quantum_plugin.OVSQuantumPlugin"

#config files (some only modified if running as openvswitch)
PLUGIN_CONF = "plugins.ini"
QUANTUM_CONF = 'quantum.conf'
PLUGIN_LOC = ['etc']
AGENT_CONF = 'ovs_quantum_plugin.ini'
AGENT_LOC = ["etc", "quantum", "plugins", "openvswitch"]
AGENT_BIN_LOC = ["quantum", "plugins", "openvswitch", 'agent']
CONFIG_FILES = [PLUGIN_CONF, AGENT_CONF]

#this db will be dropped and created
DB_NAME = 'ovs_quantum'

#opensvswitch bridge setup/teardown/name commands
OVS_BRIDGE_DEL = ['ovs-vsctl', '--no-wait', '--', '--if-exists', 'del-br', '%OVS_BRIDGE%']
OVS_BRIDGE_ADD = ['ovs-vsctl', '--no-wait', 'add-br', '%OVS_BRIDGE%']
OVS_BRIDGE_EXTERN_ID = ['ovs-vsctl', '--no-wait', 'br-set-external-id', '%OVS_BRIDGE%', 'bridge-id', '%OVS_EXTERNAL_ID%']

#id
TYPE = settings.QUANTUM

#special component options
QUANTUM_SERVICE = 'q-svc'
QUANTUM_AGENT = 'q-agt'

#subdirs of the downloaded
CONFIG_DIR = 'etc'
BIN_DIR = 'bin'

#what to start (only if openvswitch enabled)
APP_Q_SERVER = 'quantum-server'
APP_Q_AGENT = 'ovs_quantum_agent.py'
APP_OPTIONS = {
    APP_Q_SERVER: ["%QUANTUM_CONFIG_FILE%"],
    APP_Q_AGENT: ["%OVS_CONFIG_FILE%", "-v"],
}


class QuantumUninstaller(comp.PkgUninstallComponent):
    def __init__(self, *args, **kargs):
        comp.PkgUninstallComponent.__init__(self, TYPE, *args, **kargs)


class QuantumInstaller(comp.PkgInstallComponent):
    def __init__(self, *args, **kargs):
        comp.PkgInstallComponent.__init__(self, TYPE, *args, **kargs)
        self.git_loc = self.cfg.get("git", "quantum_repo")
        self.git_branch = self.cfg.get("git", "quantum_branch")
        self.q_vswitch_agent = False
        self.q_vswitch_service = False
        plugin = self.cfg.get("quantum", "q_plugin")
        if plugin == VSWITCH_PLUGIN:
            if len(self.component_opts) == 0:
                #default to on if not specified
                self.q_vswitch_agent = True
                self.q_vswitch_service = True
            else:
                #only turn on if requested
                if QUANTUM_SERVICE in self.component_opts:
                    self.q_vswitch_service = True
                if QUANTUM_AGENT in self.component_opts:
                    self.q_vswitch_agent = True

    def _get_download_locations(self):
        places = comp.PkgInstallComponent._get_download_locations(self)
        places.append({
            'uri': self.git_loc,
            'branch': self.git_branch,
        })
        return places

    def _get_pkglist(self):
        pkglist = comp.PkgInstallComponent._get_pkglist(self)
        if self.q_vswitch_service:
            listing_fn = sh.joinpths(settings.STACK_PKG_DIR, PKG_VSWITCH)
            pkglist = utils.extract_pkg_list([listing_fn], self.distro, pkglist)
        return pkglist

    def _get_config_files(self):
        parent_list = comp.PkgInstallComponent._get_config_files(self)
        parent_list.extend(CONFIG_FILES)
        return parent_list

    def _get_target_config_name(self, config_fn):
        if config_fn == PLUGIN_CONF:
            tgt_loc = [self.appdir] + PLUGIN_LOC + [config_fn]
            return sh.joinpths(*tgt_loc)
        elif config_fn == AGENT_CONF:
            tgt_loc = [self.appdir] + AGENT_LOC + [config_fn]
            return sh.joinpths(*tgt_loc)
        else:
            return comp.PkgInstallComponent._get_target_config_name(self, config_fn)

    def _config_adjust(self, contents, config_fn):
        if config_fn == PLUGIN_CONF and self.q_vswitch_service:
            #need to fix the "Quantum plugin provider module"
            newcontents = contents
            with io.BytesIO(contents) as stream:
                config = cfg.IgnoreMissingConfigParser()
                config.readfp(stream)
                provider = config.get("PLUGIN", "provider")
                if provider and provider != V_PROVIDER:
                    config.set("PLUGIN", "provider", V_PROVIDER)
                    with io.BytesIO() as outputstream:
                        config.write(outputstream)
                        outputstream.flush()
                        #TODO can we write to contents here directly?
                        newcontents = outputstream.getvalue()
            return newcontents
        elif config_fn == AGENT_CONF and self.q_vswitch_agent:
            #Need to adjust the sql connection
            newcontents = contents
            with io.BytesIO(contents) as stream:
                config = cfg.IgnoreMissingConfigParser()
                config.readfp(stream)
                db_dsn = config.get("DATABASE", "sql_connection")
                if db_dsn:
                    generated_dsn = self.cfg.get_dbdsn(DB_NAME)
                    if generated_dsn != db_dsn:
                        config.set("DATABASE", "sql_connection", generated_dsn)
                        with io.BytesIO() as outputstream:
                            config.write(outputstream)
                            outputstream.flush()
                            #TODO can we write to contents here directly?
                            newcontents = outputstream.getvalue()
            return newcontents
        else:
            return comp.PkgInstallComponent._config_adjust(self, contents, config_fn)

    def _setup_bridge(self):
        bridge = self.cfg.get("quantum", "ovs_bridge")
        if bridge:
            LOG.info("Fixing up ovs bridge named %s", bridge)
            external_id = self.cfg.get("quantum", 'ovs_bridge_external_name')
            if not external_id:
                external_id = bridge
            params = dict()
            params['OVS_BRIDGE'] = bridge
            params['OVS_EXTERNAL_ID'] = external_id
            cmds = list()
            cmds.append({
                'cmd': OVS_BRIDGE_DEL
            })
            cmds.append({
                'cmd': OVS_BRIDGE_ADD
            })
            cmds.append({
                'cmd': OVS_BRIDGE_EXTERN_ID
            })
            utils.execute_template(*cmds, params=params)
            #TODO maybe have a trace that says we did this so that we can remove it on uninstall?

    def post_install(self):
        parent_result = comp.PkgInstallComponent.post_install(self)
        if self.q_vswitch_service and settings.DB in self.instances:
            self._setup_db()
        if self.q_vswitch_agent:
            self._setup_bridge()
        return parent_result

    def _setup_db(self):
        LOG.info("Fixing up database named %s", DB_NAME)
        db.drop_db(self.cfg, DB_NAME)
        db.create_db(self.cfg, DB_NAME)

    def _get_source_config(self, config_fn):
        if config_fn == PLUGIN_CONF:
            srcloc = [self.appdir] + PLUGIN_LOC + [config_fn]
            srcfn = sh.joinpths(*srcloc)
            contents = sh.load_file(srcfn)
            return (srcfn, contents)
        elif config_fn == AGENT_CONF:
            srcloc = [self.appdir] + AGENT_LOC + [config_fn]
            srcfn = sh.joinpths(*srcloc)
            contents = sh.load_file(srcfn)
            return (srcfn, contents)
        else:
            return comp.PkgInstallComponent._get_source_config(self, config_fn)


class QuantumRuntime(comp.ProgramRuntime):
    def __init__(self, *args, **kargs):
        comp.ProgramRuntime.__init__(self, TYPE, *args, **kargs)
        self.q_vswitch_agent = False
        self.q_vswitch_service = False
        plugin = self.cfg.get("quantum", "q_plugin")
        if plugin == VSWITCH_PLUGIN:
            if len(self.component_opts) == 0:
                #default to on if not specified
                self.q_vswitch_agent = True
                self.q_vswitch_service = True
            else:
                #only turn on if requested
                if QUANTUM_SERVICE in self.component_opts:
                    self.q_vswitch_service = True
                if QUANTUM_AGENT in self.component_opts:
                    self.q_vswitch_agent = True

    def _get_apps_to_start(self):
        app_list = comp.ProgramRuntime._get_apps_to_start(self)
        if self.q_vswitch_service:
            app_list.append({
                    'name': APP_Q_SERVER,
                    'path': sh.joinpths(self.appdir, BIN_DIR, APP_Q_SERVER),
            })
        if self.q_vswitch_agent:
            full_pth = [self.appdir] + AGENT_BIN_LOC + [APP_Q_AGENT]
            app_list.append({
                    'name': APP_Q_AGENT,
                    'path': sh.joinpths(*full_pth)
            })
        return app_list

    def _get_app_options(self, app_name):
        return APP_OPTIONS.get(app_name)

    def _get_param_map(self, app_name):
        param_dict = comp.ProgramRuntime._get_param_map(self, app_name)
        if app_name == APP_Q_AGENT:
            tgt_loc = [self.appdir] + AGENT_LOC + [AGENT_CONF]
            param_dict['OVS_CONFIG_FILE'] = sh.joinpths(*tgt_loc)
        elif app_name == APP_Q_SERVER:
            param_dict['QUANTUM_CONFIG_FILE'] = sh.joinpths(self.appdir, CONFIG_DIR, QUANTUM_CONF)
        return param_dict


def describe(opts=None):
    description = """
 Module: {module_name}
  Description:
   {description}
  Component options:
   {component_opts}
"""
    params = dict()
    params['component_opts'] = "TBD"
    params['module_name'] = __name__
    params['description'] = __doc__ or "Handles actions for the quantum component."
    out = description.format(**params)
    return out.strip("\n")
