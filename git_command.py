#
# Copyright (C) 2008 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function
import fcntl
import os
import select
import sys
import subprocess
import tempfile
from signal import SIGTERM
from error import GitError
from trace import REPO_TRACE, IsTrace, Trace
from wrapper import Wrapper

GIT = 'git'
MIN_GIT_VERSION = (1, 5, 4)
GIT_DIR = 'GIT_DIR'

LAST_GITDIR = None
LAST_CWD = None

"""
以下关于ssh操作的部分不太明白，不影响对整理的理解，暂时略过
"""
_ssh_proxy_path = None
_ssh_sock_path = None
_ssh_clients = []

def ssh_sock(create=True):
  global _ssh_sock_path
  if _ssh_sock_path is None:
    if not create:
      return None
    tmp_dir = '/tmp'
    if not os.path.exists(tmp_dir):
      tmp_dir = tempfile.gettempdir()
    _ssh_sock_path = os.path.join(
      tempfile.mkdtemp('', 'ssh-', tmp_dir),
      'master-%r@%h:%p')
  return _ssh_sock_path

def _ssh_proxy():
  global _ssh_proxy_path
  if _ssh_proxy_path is None:
    _ssh_proxy_path = os.path.join(
      os.path.dirname(__file__),
      'git_ssh')
  return _ssh_proxy_path

def _add_ssh_client(p):
  _ssh_clients.append(p)

def _remove_ssh_client(p):
  try:
    _ssh_clients.remove(p)
  except ValueError:
    pass

def terminate_ssh_clients():
  global _ssh_clients
  for p in _ssh_clients:
    try:
      os.kill(p.pid, SIGTERM)
      p.wait()
    except OSError:
      pass
  _ssh_clients = []

_git_version = None

class _sfd(object):
  """select file descriptor class"""
  def __init__(self, fd, dest, std_name):
    assert std_name in ('stdout', 'stderr')
    self.fd = fd
    self.dest = dest
    self.std_name = std_name
  def fileno(self):
    return self.fd.fileno()

class _GitCall(object):
  """
  返回git的版本号字符串，如：'git version 2.7.4'

  执行'git --version'命令，并返回其输出。
  """
  def version(self):
    p = GitCommand(None, ['--version'], capture_stdout=True)
    if p.Wait() == 0:
      if hasattr(p.stdout, 'decode'):
        return p.stdout.decode('utf-8')
      else:
        return p.stdout
    return None

  """
  以tuple方式返回git的版本号，如'git version 2.7.4'，返回(2,7,4)
  """
  def version_tuple(self):
    global _git_version
    if _git_version is None:
      ver_str = git.version()
      """
      使用repo脚本的ParseGitVersion()函数解析git版本号。
      """
      _git_version = Wrapper().ParseGitVersion(ver_str)
      if _git_version is None:
        print('fatal: "%s" unsupported' % ver_str, file=sys.stderr)
        sys.exit(1)
    return _git_version

  """
  __getattr__(self, name) 方法在属性name不存在的时候被调用.
  这里通过__getattr__将_GitCall对象的属性转换为相应命令来执行, 该操作返回一个包装器函数func,
  例如对"xxx-yyy"命令的调用为: git.xxx_yyy(cmdv) --> func(cmdv) --> "git xxx-yyy cmdv"

  例如: ./project.py: self.bare_git.rev_parse('FETCH_HEAD'))
    - git.rev_parse返回一个函数func,
    - 执行git.rev_parse('FETCH_HEAD')相当于执行函数func('FETCH_HEAD'),
    - func('FETCH_HEAD')函数执行git命令"git rev-parse FETCH_HEAD"
  例如: ./project.py: self.bare_git.pack_refs('--all', '--prune')
    - git.pack_refs返回一个函数func,
    - 执行git.pack_refs('--all', '--prune')相当于执行函数func('--all', '--prune')
    - func('--all', '--prune')函数执行git命令"git pack-refs --all --prune"
  """
  def __getattr__(self, name):
    name = name.replace('_','-')
    def fun(*cmdv):
      command = [name]
      command.extend(cmdv)
      return GitCommand(None, command).Wait() == 0
    return fun
git = _GitCall()

"""
检查git版本是否满足最小版本min_version

在fail=True的情况下，如果不满足最小版本要求，则显示警告信息并退出。
"""
def git_require(min_version, fail=False):
  git_version = git.version_tuple()
  if min_version <= git_version:
    return True
  if fail:
    need = '.'.join(map(str, min_version))
    print('fatal: git %s or later required' % need, file=sys.stderr)
    sys.exit(1)
  return False

def _setenv(env, name, value):
  env[name] = value.encode()

"""
GitCommand用于执行git命令并捕获其输出(标准输出和标准错误输出)

包含两个操作：
- 初始化: GitCommand()
- 等待: Wait()
使用方式如下： (以'git --version'为例)
  p = GitCommand(None, ['--version'], capture_stdout=True, capture_stderr=True)
  p.Wait()
  使用p.stdout访问stdout的内容
  使用p.stderr访问stderr的内容
"""
class GitCommand(object):
  def __init__(self,
               project,
               cmdv,
               bare = False,
               provide_stdin = False,
               capture_stdout = False,
               capture_stderr = False,
               disable_editor = False,
               ssh_proxy = False,
               cwd = None,
               gitdir = None):
    env = os.environ.copy()

    """
    从复制的环境变量中清除以下跟GIT相关的变量。
    """
    for key in [REPO_TRACE,
              GIT_DIR,
              'GIT_ALTERNATE_OBJECT_DIRECTORIES',
              'GIT_OBJECT_DIRECTORY',
              'GIT_WORK_TREE',
              'GIT_GRAFT_FILE',
              'GIT_INDEX_FILE']:
      if key in env:
        del env[key]

    """
    根据capture_stdout和capture_stderr决定是否需要抓取标准输出和标准错误输出
    默认:
    - tee['stdout'] = True
    - tee['stderr'] = True
    """
    # If we are not capturing std* then need to print it.
    self.tee = {'stdout': not capture_stdout, 'stderr': not capture_stderr}

    """
    添加以下环境变量：
    - 'GIT_EDITOR'
    - 'REPO_SSH_SOCK'
    - 'GIT_SSH'
    - 'GIT_CONFIG_PARAMETERS'
    - 'GIT_ALLOW_PROTOCOL'
    """
    if disable_editor:
      _setenv(env, 'GIT_EDITOR', ':')
    if ssh_proxy:
      _setenv(env, 'REPO_SSH_SOCK', ssh_sock())
      _setenv(env, 'GIT_SSH', _ssh_proxy())
    if 'http_proxy' in env and 'darwin' == sys.platform:
      s = "'http.proxy=%s'" % (env['http_proxy'],)
      p = env.get('GIT_CONFIG_PARAMETERS')
      if p is not None:
        s = p + ' ' + s
      _setenv(env, 'GIT_CONFIG_PARAMETERS', s)
    if 'GIT_ALLOW_PROTOCOL' not in env:
      _setenv(env, 'GIT_ALLOW_PROTOCOL',
              'file:git:http:https:ssh:persistent-http:persistent-https:sso:rpc')

    """
    设置git命令执行的路径(cwd)和相应的'.git'目录(gitdir)
    """
    if project:
      if not cwd:
        cwd = project.worktree
      if not gitdir:
        gitdir = project.gitdir

    command = [GIT]
    if bare:
      if gitdir:
        _setenv(env, GIT_DIR, gitdir)
      cwd = None

    command.append(cmdv[0])
    # Need to use the --progress flag for fetch/clone so output will be
    # displayed as by default git only does progress output if stderr is a TTY.
    """
    对于'git fetch'和'git clone'命令，添加'--progress'选项。
    """
    if sys.stderr.isatty() and cmdv[0] in ('fetch', 'clone'):
      if '--progress' not in cmdv and '--quiet' not in cmdv:
        command.append('--progress')
    """
    生成完整git命令，如：
    'git init'
    'git config --file /path/to/test/.repo/manifests.git/config --null --list'
    """
    command.extend(cmdv[1:])

    if provide_stdin:
      stdin = subprocess.PIPE
    else:
      stdin = None

    stdout = subprocess.PIPE
    stderr = subprocess.PIPE

    """
    跟踪cwd和gitdir的变化
    """
    if IsTrace():
      global LAST_CWD
      global LAST_GITDIR

      dbg = ''

      if cwd and LAST_CWD != cwd:
        if LAST_GITDIR or LAST_CWD:
          dbg += '\n'
        dbg += ': cd %s\n' % cwd
        LAST_CWD = cwd

      if GIT_DIR in env and LAST_GITDIR != env[GIT_DIR]:
        if LAST_GITDIR or LAST_CWD:
          dbg += '\n'
        dbg += ': export GIT_DIR=%s\n' % env[GIT_DIR]
        LAST_GITDIR = env[GIT_DIR]

      """
      格式化git命令在串口的输出，如：
      ': git init 1>| 2>|'
      ': git --version 1>| 2>|'
      ': git fetch origin --tags +refs/heads/*:refs/remotes/origin/* +refs/heads/3.2.0:refs/remotes/origin/3.2.0 1>| 2>|'
      """
      dbg += ': '
      dbg += ' '.join(command)
      if stdin == subprocess.PIPE:
        dbg += ' 0<|'
      if stdout == subprocess.PIPE:
        dbg += ' 1>|'
      if stderr == subprocess.PIPE:
        dbg += ' 2>|'
      Trace('%s', dbg)

    """
    执行git命令
    """
    try:
      p = subprocess.Popen(command,
                           cwd = cwd,
                           env = env,
                           stdin = stdin,
                           stdout = stdout,
                           stderr = stderr)
    except Exception as e:
      raise GitError('%s: %s' % (command[1], e))

    if ssh_proxy:
      _add_ssh_client(p)

    self.process = p
    self.stdin = p.stdin

  """
  等待git命令的执行，并返回其输出内容
  """
  def Wait(self):
    try:
      p = self.process
      rc = self._CaptureOutput()
    finally:
      _remove_ssh_client(p)
    return rc

  """
  抓取git命令的输出
  """
  def _CaptureOutput(self):
    p = self.process
    s_in = [_sfd(p.stdout, sys.stdout, 'stdout'),
            _sfd(p.stderr, sys.stderr, 'stderr')]
    self.stdout = ''
    self.stderr = ''

    for s in s_in:
      flags = fcntl.fcntl(s.fd, fcntl.F_GETFL)
      fcntl.fcntl(s.fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    while s_in:
      in_ready, _, _ = select.select(s_in, [], [])
      for s in in_ready:
        buf = s.fd.read(4096)
        if not buf:
          s_in.remove(s)
          continue
        if not hasattr(buf, 'encode'):
          buf = buf.decode()
        if s.std_name == 'stdout':
          self.stdout += buf
        else:
          self.stderr += buf
        if self.tee[s.std_name]:
          s.dest.write(buf)
          s.dest.flush()
    return p.wait()
