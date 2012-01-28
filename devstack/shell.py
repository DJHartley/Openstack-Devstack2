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

import getpass
import grp
import os
import os.path
import pwd
import shutil
import subprocess

from devstack import env
from devstack import exceptions as excp
from devstack import log as logging

ROOT_HELPER = ["sudo"]
MKPW_CMD = ["openssl", 'rand', '-hex']
PASS_ASK_ENV = 'PASS_ASK'
LOG = logging.getLogger("devstack.shell")


def execute(*cmd, **kwargs):

    process_input = kwargs.pop('process_input', None)
    check_exit_code = kwargs.pop('check_exit_code', [0])
    cwd = kwargs.pop('cwd', None)
    env_overrides = kwargs.pop('env_overrides', None)
    ignore_exit_code = False

    if isinstance(check_exit_code, bool):
        ignore_exit_code = not check_exit_code
        check_exit_code = [0]
    elif isinstance(check_exit_code, int):
        check_exit_code = [check_exit_code]

    run_as_root = kwargs.pop('run_as_root', False)
    shell = kwargs.pop('shell', False)
    if run_as_root:
        cmd = ROOT_HELPER + list(cmd)

    cmd = [str(c) for c in cmd]
    execute_cmd = None
    str_cmd = " ".join(cmd)
    if shell:
        execute_cmd = str_cmd.strip()
        LOG.debug('Running shell cmd: [%s]' % (str_cmd))
    else:
        execute_cmd = cmd
        LOG.debug('Running cmd: [%s]' % (str_cmd))

    if process_input is not None:
        LOG.debug('With stdin: %s' % (process_input))

    if cwd:
        LOG.debug("In working directory: %s" % (cwd))

    stdin_fh = subprocess.PIPE
    stdout_fh = subprocess.PIPE
    stderr_fh = subprocess.PIPE
    close_file_descriptors = True

    if 'stdout_fh' in kwargs.keys():
        stdout_fh = kwargs.get('stdout_fh')
        LOG.debug("Redirecting stdout to file handle: %s" % (stdout_fh))

    if 'stdin_fh' in kwargs.keys():
        stdin_fh = kwargs.get('stdin_fh')
        LOG.debug("Redirecting stdin to file handle: %s" % (stdin_fh))
        process_input = None

    if 'stderr_fh' in kwargs.keys():
        stderr_fh = kwargs.get('stderr_fh')
        LOG.debug("Redirecting stderr to file handle: %s" % (stderr_fh))

    process_env = env.get()
    LOG.debug("With environment: %s" % (process_env))
    if env_overrides and len(env_overrides):
        LOG.debug("With additional environment overrides: %s" % (env_overrides))
        for (k, v) in env_overrides.items():
            process_env[k] = str(v)

    obj = subprocess.Popen(execute_cmd,
            stdin=stdin_fh,
            stdout=stdout_fh,
            stderr=stderr_fh,
            close_fds=close_file_descriptors,
            cwd=cwd,
            shell=shell,
            env=process_env)

    result = None
    if process_input is not None:
        result = obj.communicate(str(process_input))
    else:
        result = obj.communicate()

    if 'stdin_fh' not in kwargs.keys():
        obj.stdin.close()

    rc = obj.returncode
    LOG.debug('Cmd result had exit code: %s' % rc)

    if (not ignore_exit_code) and (rc not in check_exit_code):
        (stdout, stderr) = result
        raise excp.ProcessExecutionError(
                exit_code=rc,
                stdout=stdout,
                stderr=stderr,
                cmd=str_cmd)
    else:
        #log it anyway
        if rc not in check_exit_code:
            (stdout, stderr) = result
            LOG.debug("A failure may of just happened when running command \"%s\" [%s] (%s, %s)", str_cmd,
                rc, stdout.strip(), stderr.strip())
        return result


def listdir(path):
    return os.listdir(path)


def isfile(fn):
    return os.path.isfile(fn)


def isdir(path):
    return os.path.isdir(path)


def joinpths(*paths):
    return os.path.join(*paths)


def _gen_password(pw_len):
    if pw_len <= 0:
        msg = "Password length %s can not be less than or equal to zero" % (pw_len)
        raise excp.BadParamException(msg)
    LOG.debug("Generating you a pseudo-random password of byte length: %s" % (pw_len))
    cmd = MKPW_CMD + [pw_len]
    (stdout, _) = execute(*cmd)
    return stdout.strip()


def _prompt_password(prompt_=None):
    if prompt_:
        return getpass.getpass(prompt_)
    else:
        return getpass.getpass()


def chown_r(path, uid, gid):
    if(isdir(path)):
        for root, dirs, files in os.walk(path):
            os.chown(root, uid, gid)
            for d in dirs:
                os.chown(joinpths(root, d), uid, gid)
            for f in files:
                os.chown(joinpths(root, f), uid, gid)


def password(prompt_=None, pw_len=8):
    rd = ""
    ask_for_pw = env.get_bool(PASS_ASK_ENV, True)
    if ask_for_pw:
        rd = _prompt_password(prompt_)
    if not rd:
        return _gen_password(pw_len)
    else:
        return rd


def mkdirslist(path):
    LOG.debug("Determining potential paths to create for target path \"%s\"" % (path))
    dirs_possible = set()
    dirs_possible.add(path)

    while True:
        (base, _) = os.path.split(path)
        dirs_possible.add(base)
        path = base
        if path == os.sep:
            break
    #sorting is important so that we go in the right order.. (/ before /tmp and so on)
    dirs_made = list()
    for check_path in sorted(dirs_possible):
        if not isdir(check_path):
            mkdir(check_path, False)
            dirs_made.append(check_path)
    return dirs_made


def append_file(fn, text, flush=True, quiet=False):
    if not quiet:
        LOG.debug("Appending to file %s (%d bytes)", fn, len(text))
    with open(fn, "a") as f:
        f.write(text)
        if flush:
            f.flush()


def write_file(fn, text, flush=True, quiet=False):
    if not quiet:
        LOG.debug("Writing to file %s (%d bytes)", fn, len(text))
    with open(fn, "w") as f:
        f.write(text)
        if flush:
            f.flush()


def touch_file(fn, die_if_there=True, quiet=False, file_size=0):
    if not isfile(fn):
        if not quiet:
            LOG.debug("Touching and truncating file %s", fn)
        with open(fn, "w") as f:
            f.truncate(file_size)
    else:
        if die_if_there:
            msg = "Can not touch file %s since it already exists" % (fn)
            raise excp.FileException(msg)


def load_file(fn, quiet=False):
    if not quiet:
        LOG.debug("Loading data from file %s", fn)
    data = ""
    with open(fn, "r") as f:
        data = f.read()
    if not quiet:
        LOG.debug("Loaded (%d) bytes from file %s", len(data), fn)
    return data


def mkdir(path, recurse=True):
    if not isdir(path):
        if recurse:
            LOG.debug("Recursively creating directory \"%s\"" % (path))
            os.makedirs(path)
        else:
            LOG.debug("Creating directory \"%s\"" % (path))
            os.mkdir(path)


def deldir(path):
    if isdir(path):
        LOG.debug("Recursively deleting directory tree starting at \"%s\"" % (path))
        shutil.rmtree(path)


def rmdir(path, quiet=True):
    if not isdir(path):
        return
    try:
        LOG.debug("Deleting directory \"%s\" with the cavet that we will fail if it's not empty." % (path))
        os.rmdir(path)
        LOG.debug("Deleted directory \"%s\"" % (path))
    except OSError:
        if not quiet:
            raise
        else:
            pass


def dirname(path):
    return os.path.dirname(path)


def canon_path(path):
    return os.path.realpath(path)


def prompt(prompt_str):
    return raw_input(prompt_str)


def getuser():
    return getpass.getuser()


def getuid(username):
    uinfo = pwd.getpwnam(username)
    return uinfo.pw_gid


def getgid(groupname):
    grp_info = grp.getgrnam(groupname)
    return grp_info.gr_gid


def getgroupname(gid=None):
    if(gid is None):
        gid = os.getgid()
    gid_info = grp.getgrgid(gid)
    return gid_info.gr_name


def unlink(path, ignore_errors=True):
    try:
        LOG.debug("Unlinking (removing) %s" % (path))
        os.unlink(path)
    except OSError:
        if not ignore_errors:
            raise
        else:
            pass
