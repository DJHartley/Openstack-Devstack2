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

from devstack import downloader as down
from devstack import exceptions as excp
from devstack import log as logging
from devstack import pip
from devstack import settings
from devstack import shell as sh
from devstack import trace as tr
from devstack import utils

from devstack.runners import fork
from devstack.runners import screen


LOG = logging.getLogger("devstack.component")

#how we actually setup and unsetup python
PY_INSTALL = ['python', 'setup.py', 'develop']
PY_UNINSTALL = ['python', 'setup.py', 'develop', '--uninstall']

#runtime status constants (return by runtime status)
STATUS_UNKNOWN = "unknown"
STATUS_STARTED = "started"
STATUS_STOPPED = "stopped"


class ComponentBase(object):
    def __init__(self, component_name, **kargs):
        self.cfg = kargs.get("config")
        self.packager = kargs.get("packager")
        self.distro = kargs.get("distro")
        self.root = kargs.get("root")
        self.instances = kargs.get("instances")
        self.component_opts = kargs.get('opts')
        self.component_root = sh.joinpths(self.root, component_name)
        self.tracedir = sh.joinpths(self.component_root, settings.COMPONENT_TRACE_DIR)
        self.appdir = sh.joinpths(self.component_root, settings.COMPONENT_APP_DIR)
        self.cfgdir = sh.joinpths(self.component_root, settings.COMPONENT_CONFIG_DIR)
        self.component_name = component_name


class PkgInstallComponent(ComponentBase):
    def __init__(self, component_name, *args, **kargs):
        ComponentBase.__init__(self, component_name, *args, **kargs)
        self.tracewriter = tr.TraceWriter(self.tracedir, tr.IN_TRACE)

    def _get_download_locations(self):
        return list()

    def download(self):
        locations = self._get_download_locations()
        base_dir = self.appdir
        am_downloaded = 0
        for location_info in locations:
            uri = location_info.get("uri")
            if not uri:
                continue
            branch = location_info.get("branch")
            subdir = location_info.get("subdir")
            target_loc = None
            if subdir and len(subdir):
                target_loc = sh.joinpths(base_dir, subdir)
            else:
                target_loc = base_dir
            dirsmade = down.download(target_loc, uri, branch)
            self.tracewriter.downloaded(target_loc, uri)
            self.tracewriter.dir_made(*dirsmade)
            am_downloaded += 1
        return am_downloaded

    def _get_param_map(self, config_fn):
        return None

    def _get_pkglist(self):
        return utils.get_pkg_list(self.distro, self.component_name)

    def install(self):
        pkgs = self._get_pkglist()
        if pkgs:
            pkgnames = sorted(pkgs.keys())
            LOG.info("Installing packages (%s)." % (", ".join(pkgnames)))
            self.packager.install_batch(pkgs)
            #add trace used to remove the pkgs
            for name in pkgnames:
                self.tracewriter.package_install(name, pkgs.get(name))
        return self.tracedir

    def pre_install(self):
        pkgs = utils.get_pkg_list(self.distro, self.component_name)
        if pkgs:
            mp = self._get_param_map(None)
            self.packager.pre_install(pkgs, mp)
        return self.tracedir

    def post_install(self):
        pkgs = utils.get_pkg_list(self.distro, self.component_name)
        if pkgs:
            mp = self._get_param_map(None)
            self.packager.post_install(pkgs, mp)
        return self.tracedir

    def _get_config_files(self):
        return list()

    def _config_adjust(self, contents, config_fn):
        return contents

    def _get_target_config_name(self, config_fn):
        return sh.joinpths(self.cfgdir, config_fn)

    def _get_source_config(self, config_fn):
        return utils.load_template(self.component_name, config_fn)

    def _configure_files(self):
        configs = self._get_config_files()
        if configs:
            LOG.info("Configuring %s files" % (len(configs)))
            for fn in configs:
                #get the params and where it should come from and where it should go
                parameters = self._get_param_map(fn)
                tgtfn = self._get_target_config_name(fn)
                #ensure directory is there (if not created previously)
                self.tracewriter.make_dir(sh.dirname(tgtfn))
                #now configure it
                LOG.info("Configuring file %s" % (fn))
                (sourcefn, contents) = self._get_source_config(fn)
                LOG.info("Replacing parameters in file %s" % (sourcefn))
                LOG.debug("Replacements = %s" % (parameters))
                contents = utils.param_replace(contents, parameters)
                LOG.debug("Applying side-effects of param replacement for template %s" % (sourcefn))
                contents = self._config_adjust(contents, fn)
                LOG.info("Writing configuration file %s" % (tgtfn))
                #this trace is used to remove the files configured
                sh.write_file(tgtfn, contents)
                self.tracewriter.cfg_write(tgtfn)
        return len(configs)

    def configure(self):
        self.tracewriter.make_dir(self.cfgdir)
        files_configured = self._configure_files()
        return files_configured


class PythonInstallComponent(PkgInstallComponent):
    def __init__(self, component_name, *args, **kargs):
        PkgInstallComponent.__init__(self, component_name, *args, **kargs)

    def _get_python_directories(self):
        py_dirs = list()
        py_dirs.append({
            'name': self.component_name,
            'work_dir': self.appdir,
        })
        return py_dirs

    def _get_pip_list(self):
        return utils.get_pip_list(self.distro, self.component_name)

    def _install_pips(self):
        #install any need pip items
        pips = self._get_pip_list()
        if pips:
            LOG.info("Setting up %s pips (%s)" % (len(pips), ", ".join(pips.keys())))
            pip.install(pips)
            for name in pips.keys():
                self.tracewriter.pip_install(name, pips.get(name))

    def _format_stderr_out(self, stderr, stdout):
        if stdout is None:
            stdout = ''
        if stderr is None:
            stderr = ''
        combined = ["===STDOUT===", str(stdout), "===STDERR===", str(stderr)]
        return utils.joinlinesep(*combined)

    def _format_trace_name(self, name):
        return "%s-%s" % (tr.PY_TRACE, name)

    def _install_python_setups(self):
        #setup any python directories
        pydirs = self._get_python_directories()
        if pydirs:
            actual_dirs = list()
            for pydir_info in pydirs:
                working_dir = pydir_info.get('work_dir', self.appdir)
                actual_dirs.append(working_dir)
            LOG.info("Setting up %s python directories (%s)" % (len(pydirs), ", ".join(actual_dirs)))
            self.tracewriter.make_dir(self.tracedir)
            for pydir_info in pydirs:
                name = pydir_info.get("name")
                if not name:
                    #TODO should we raise an exception here?
                    continue
                working_dir = pydir_info.get('work_dir', self.appdir)
                record_fn = tr.touch_trace(self.tracedir, self._format_trace_name(name))
                self.tracewriter.file_touched(record_fn)
                (stdout, stderr) = sh.execute(*PY_INSTALL, cwd=working_dir, run_as_root=True)
                sh.write_file(record_fn, self._format_stderr_out(stderr, stdout))
                self.tracewriter.py_install(name, record_fn, working_dir)

    def _python_install(self):
        self._install_pips()
        self._install_python_setups()

    def install(self):
        parent_result = PkgInstallComponent.install(self)
        self._python_install()
        return parent_result


class PkgUninstallComponent(ComponentBase):
    def __init__(self, component_name, *args, **kargs):
        ComponentBase.__init__(self, component_name, *args, **kargs)
        self.tracereader = tr.TraceReader(self.tracedir, tr.IN_TRACE)

    def unconfigure(self):
        self._unconfigure_files()

    def _unconfigure_files(self):
        cfgfiles = self.tracereader.files_configured()
        if cfgfiles:
            LOG.info("Removing %s configuration files (%s)" % (len(cfgfiles), ", ".join(cfgfiles)))
            for fn in cfgfiles:
                if fn:
                    sh.unlink(fn)

    def uninstall(self):
        self._uninstall_pkgs()
        self._uninstall_touched_files()
        self._uninstall_dirs()

    def _uninstall_pkgs(self):
        pkgsfull = self.tracereader.packages_installed()
        if pkgsfull:
            LOG.info("Potentially removing %s packages (%s)" % (len(pkgsfull), ", ".join(sorted(pkgsfull.keys()))))
            which_removed = self.packager.remove_batch(pkgsfull)
            LOG.info("Actually removed %s packages (%s)" % (len(which_removed), ", ".join(sorted(which_removed))))

    def _uninstall_touched_files(self):
        filestouched = self.tracereader.files_touched()
        if filestouched:
            LOG.info("Removing %s touched files (%s)" % (len(filestouched), ", ".join(filestouched)))
            for fn in filestouched:
                if fn:
                    sh.unlink(fn)

    def _uninstall_dirs(self):
        dirsmade = self.tracereader.dirs_made()
        if dirsmade:
            LOG.info("Removing %s created directories (%s)" % (len(dirsmade), ", ".join(dirsmade)))
            for dirname in dirsmade:
                sh.deldir(dirname)


class PythonUninstallComponent(PkgUninstallComponent):
    def __init__(self, component_name, *args, **kargs):
        PkgUninstallComponent.__init__(self, component_name, *args, **kargs)

    def uninstall(self):
        self._uninstall_pkgs()
        self._uninstall_pips()
        self._uninstall_touched_files()
        self._uninstall_python()
        self._uninstall_dirs()

    def _uninstall_pips(self):
        pips = self.tracereader.pips_installed()
        if pips:
            LOG.info("Uninstalling %s pips" % (len(pips)))
            pip.uninstall(pips)

    def _uninstall_python(self):
        pylisting = self.tracereader.py_listing()
        if pylisting:
            LOG.info("Uninstalling %s python setups" % (len(pylisting)))
            for entry in pylisting:
                where = entry.get('where')
                sh.execute(*PY_UNINSTALL, cwd=where, run_as_root=True)


class ProgramRuntime(ComponentBase):
    #this here determines how we start and stop and
    #what classes handle different running/stopping types
    STARTER_CLS_MAPPING = {
        fork.RUN_TYPE: fork.ForkRunner,
        screen.RUN_TYPE: screen.ScreenRunner,
    }
    STOPPER_CLS_MAPPING = {
        fork.RUN_TYPE: fork.ForkRunner,
        screen.RUN_TYPE: screen.ScreenRunner,
    }

    def __init__(self, component_name, *args, **kargs):
        ComponentBase.__init__(self, component_name, *args, **kargs)
        self.run_type = kargs.get("run_type", fork.RUN_TYPE)
        self.tracereader = tr.TraceReader(self.tracedir, tr.IN_TRACE)
        self.tracewriter = tr.TraceWriter(self.tracedir, tr.START_TRACE)
        self.starttracereader = tr.TraceReader(self.tracedir, tr.START_TRACE)
        self.check_installed_pkgs = kargs.get("check_installed_pkgs", True)

    def _getstartercls(self, start_mode):
        if start_mode not in ProgramRuntime.STARTER_CLS_MAPPING:
            raise NotImplementedError("Can not yet start %s mode" % (start_mode))
        return ProgramRuntime.STARTER_CLS_MAPPING.get(start_mode)

    def _getstoppercls(self, stop_mode):
        if stop_mode not in ProgramRuntime.STOPPER_CLS_MAPPING:
            raise NotImplementedError("Can not yet stop %s mode" % (stop_mode))
        return ProgramRuntime.STOPPER_CLS_MAPPING.get(stop_mode)

    def _was_installed(self):
        if not self.check_installed_pkgs:
            return True
        if self.tracereader.packages_installed():
            return True
        return False

    def _get_apps_to_start(self):
        return list()

    def _get_app_options(self, app_name):
        return list()

    def _get_param_map(self, app_name):
        return {
            'ROOT': self.appdir,
        }

    def pre_start(self):
        pass

    def post_start(self):
        pass

    def start(self):
        #ensure it was installed
        if not self._was_installed():
            msg = "Can not start %s since it was not installed" % (self.component_name)
            raise excp.StartException(msg)
        #select how we are going to start it
        startercls = self._getstartercls(self.run_type)
        starter = startercls()
        #start all apps
        #this fns list will have info about what was started
        fns = list()
        apps = self._get_apps_to_start()
        for app_info in apps:
            #extract needed keys
            app_name = app_info.get("name")
            app_pth = app_info.get("path", app_name)
            app_dir = app_info.get("app_dir", self.appdir)
            #adjust the program options now that we have real locations
            params = self._get_param_map(app_name)
            program_opts = self._get_app_options(app_name)
            if params and program_opts:
                adjusted_opts = list()
                for opt in program_opts:
                    adjusted_opts.append(utils.param_replace(opt, params))
                program_opts = adjusted_opts
            LOG.info("Starting [%s] with options [%s]" % (app_name, ", ".join(program_opts)))
            #start it with the given settings
            fn = starter.start(app_name, app_pth, *program_opts, app_dir=app_dir, \
                               trace_dir=self.tracedir)
            if fn:
                fns.append(fn)
                LOG.info("Started %s, details are in %s" % (app_name, fn))
                #this trace is used to locate details about what to stop
                self.tracewriter.started_info(app_name, fn)
            else:
                LOG.info("Started %s" % (app_name))
        return fns

    def stop(self):
        #ensure it was installed
        if not self._was_installed():
            msg = "Can not stop %s since it was not installed" % (self.component_name)
            raise excp.StopException(msg)
        #we can only stop what has a started trace
        start_traces = self.starttracereader.apps_started()
        killedam = 0
        for mp in start_traces:
            #extract the apps name and where its trace is
            fn = mp.get('trace_fn')
            name = mp.get('name')
            #missing some key info, skip it
            if fn is None or name is None:
                continue
            #figure out which class will stop it
            contents = tr.parse_fn(fn)
            killcls = None
            runtype = None
            for (cmd, action) in contents:
                if cmd == "TYPE":
                    runtype = action
                    killcls = self._getstoppercls(runtype)
                    break
            #did we find a class that can do it?
            if killcls:
                #we can try to stop it
                LOG.info("Stopping %s of run type %s" % (name, runtype))
                #create an instance of the killer class and attempt to stop
                killer = killcls()
                killer.stop(name, trace_dir=self.tracedir)
                killedam += 1
            else:
                #TODO raise error??
                pass
        #if we got rid of them all get rid of the trace
        if killedam == len(start_traces):
            fn = self.starttracereader.trace_fn
            LOG.info("Deleting trace file %s" % (fn))
            sh.unlink(fn)
        return killedam

    def status(self):
        return STATUS_UNKNOWN

    def restart(self):
        return 0


class PythonRuntime(ProgramRuntime):
    def __init__(self, component_name, *args, **kargs):
        ProgramRuntime.__init__(self, component_name, *args, **kargs)

    def _was_installed(self):
        parent_result = ProgramRuntime._was_installed(self)
        if not parent_result:
            return False
        python_installed = self.tracereader.py_listing()
        if not python_installed:
            return False
        else:
            return True


class EmptyRuntime(ComponentBase):
    def __init__(self, component_name, *args, **kargs):
        ComponentBase.__init__(self, component_name, *args, **kargs)

    def pre_start(self):
        pass

    def post_start(self):
        pass

    def start(self):
        return 0

    def stop(self):
        return 0

    def status(self):
        return STATUS_UNKNOWN

    def restart(self):
        return 0
