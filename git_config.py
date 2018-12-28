# -*- coding: utf-8 -*-
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

import contextlib
import errno
import json
import os
import re
import subprocess
import sys
try:
  import threading as _threading
except ImportError:
  import dummy_threading as _threading
import time

from pyversion import is_python3
if is_python3():
  import urllib.request
  import urllib.error
else:
  import urllib2
  import imp
  urllib = imp.new_module('urllib')
  urllib.request = urllib2
  urllib.error = urllib2

from signal import SIGTERM
from error import GitError, UploadError
from trace import Trace
if is_python3():
  from http.client import HTTPException
else:
  from httplib import HTTPException

from git_command import GitCommand
from git_command import ssh_sock
from git_command import terminate_ssh_clients

R_HEADS = 'refs/heads/'
R_TAGS  = 'refs/tags/'
ID_RE = re.compile(r'^[0-9a-f]{40}$')

REVIEW_CACHE = dict()

"""
判断rev是否满足40位commit id的格式

如：'34acdd253439448b6c08c3abfc5e7b8bd03f383f'
"""
def IsId(rev):
  return ID_RE.match(rev)

"""
name为以'.'连接的字符串，_key(name)操作返回字符串首尾节的小写形式

例如：_key('Remote.Google.URL')='remote.Google.url'

类似以下的设置：
.repo/repo$ cat .git/.repo_config.json
{
  ...
  "Remote.Google.URL": [
    "https://gerrit.googlesource.com/git-repo"
  ],
  ...
}
"""
def _key(name):
  """
  对name进行分割，然后将开始和结尾的部分转换为小写，并重新连接起来
  """
  parts = name.split('.')
  if len(parts) < 2:
    return name.lower()
  parts[ 0] = parts[ 0].lower()
  parts[-1] = parts[-1].lower()
  return '.'.join(parts)

class GitConfig(object):
  _ForUser = None

  """
  通过GitConfig.ForUser()调用，返回用户级别'~/.gitconfig'的配置对象
  """
  @classmethod
  def ForUser(cls):
    if cls._ForUser is None:
      cls._ForUser = cls(configfile = os.path.expanduser('~/.gitconfig'))
    return cls._ForUser

  """
  通过GitConfig.ForRepository()调用，返回仓库级别'.git/config'的配置对象
  """
  @classmethod
  def ForRepository(cls, gitdir, defaults=None):
    return cls(configfile = os.path.join(gitdir, 'config'),
               defaults = defaults)

  """
  使用configfile指定的文件实例化GitConfig的类对象
  """
  def __init__(self, configfile, defaults=None, jsonFile=None):
    """
             .file 指向具体的config文件，如'.git/config'或'~/.gitconfig'，对于globalConfig有configfile='~/.gitconfig'
         .defaults 指向默认的config文件，如：
                   project类初始化时使用ForRepository(gitdir=self.gitdir, defaults=self.manifest.globalConfig)来进行初始化。
                   即 manifest对应的globalConfig，实际上是'~/.gitconfig'
      ._cache_dict 访问'_cache'属性时，会保存从'.git/.repo_config.json'中加载的键值对到'_cache_dict_'
    ._section_dict
         ._remotes
        ._branches
    """
    self.file = configfile
    self.defaults = defaults
    self._cache_dict = None
    self._section_dict = None
    self._remotes = {}
    self._branches = {}

    """
    默认设置 _json = '.repo/repo/.git/.repo_config.json'
    """
    self._json = jsonFile
    if self._json is None:
      self._json = os.path.join(
        os.path.dirname(self.file),
        '.repo_' + os.path.basename(self.file) + '.json')

  """
  判断name对应项是否存在

  调用示例：config.Has('user.name') 或 config.Has('user.email')
  """
  def Has(self, name, include_defaults = True):
    """Return true if this configuration file has the key.
    """
    """
    调用_key(name)对name进行处理，并检查结果是否位于_cache中
    """
    if _key(name) in self._cache:
      return True
    if include_defaults and self.defaults:
      return self.defaults.Has(name, include_defaults = True)
    return False

  """
  返回name项的bool值, true/yes: True, false/no: False
  """
  def GetBoolean(self, name):
    """Returns a boolean from the configuration file.
       None : The value was not defined, or is not a boolean.
       True : The value was set to true or yes.
       False: The value was set to false or no.
    """
    v = self.GetString(name)
    if v is None:
      return None
    v = v.lower()
    if v in ('true', 'yes'):
      return True
    if v in ('false', 'no'):
      return False
    return None

  """
  返回name项的值，如果all_keys=True，则返回所有的值，默认返回第一项值。
  """
  def GetString(self, name, all_keys=False):
    """Get the first value for a key, or None if it is not defined.

       This configuration file is used first, if the key is not
       defined or all_keys = True then the defaults are also searched.
    """
    try:
      v = self._cache[_key(name)]
    except KeyError:
      if self.defaults:
        return self.defaults.GetString(name, all_keys = all_keys)
      v = []

    if not all_keys:
      if v:
        return v[0]
      return None

    r = []
    r.extend(v)
    if self.defaults:
      r.extend(self.defaults.GetString(name, all_keys = True))
    return r

  """
  使用value更新name项对应的值, 如果value=None，则删除name项
  """
  def SetString(self, name, value):
    """Set the value(s) for a key.
       Only this configuration file is modified.

       The supplied value should be either a string,
       or a list of strings (to store multiple values).
    """
    key = _key(name)

    """
    先检查name项是否已经设置，如果有设置，将其原来的值保存在old中

    如果value=None，则删除name项, 并执行'git config --file file --unset-all name'命令

    如果value不为None，则使用value的值更新name项, 并执行以下命令更新(如果value是多项，则逐一添加每一项)：
    'git config --file file --replace-all name value[0]'
    'git config --file file --add name value[1]'
    'git config --file file --add name value[...]'
    'git config --file file --add name value[i]'

    其中'--replace-all'选项会替换所有的多行设置，默认只替换一行。

    以下是以url.xxx.insteadof配置的一个示例：
    $ cat .git/config
    ...
    [branch "default"]
      remote = origin
      merge = refs/heads/stable
    $ git config  url.http://localhost.insteadof https://gerrit.googlesource.com/git-repo
    $ git config  --add url.http://localhost.insteadof https://github.com/guyongqiangx/git-repo
    $ git config  --add url.http://localhost.insteadof https://aosp.tuna.tsinghua.edu.cn/git-repo
    $ cat .git/config
    ...
    [branch "default"]
      remote = origin
      merge = refs/heads/stable
    [url "http://localhost"]
      insteadof = https://gerrit.googlesource.com/git-repo
      insteadof = https://github.com/guyongqiangx/git-repo
      insteadof = https://aosp.tuna.tsinghua.edu.cn/git-repo
    $ git config  url.http://localhost.insteadof http://127.0.0.1/git-repo
    warning: url.http://localhost.insteadof has multiple values
    error: cannot overwrite multiple values with a single value
           Use a regexp, --add or --replace-all to change url.http://localhost.insteadof.
    $ git config  --replace-all url.http://localhost.insteadof http://127.0.0.1/git-repo
    $ cat .git/config
    ...
    [branch "default"]
      remote = origin
      merge = refs/heads/stable
    [url "http://localhost"]
      insteadof = http://127.0.0.1/git-repo
    """
    try:
      old = self._cache[key]
    except KeyError:
      old = []

    if value is None:
      if old:
        del self._cache[key]
        self._do('--unset-all', name)

    elif isinstance(value, list):
      """
      对value为list的情况，说明value包含0个或多个值，逐一使用valued的值进行更新
      """
      if len(value) == 0:
        self.SetString(name, None)

      elif len(value) == 1:
        self.SetString(name, value[0])

      elif old != value:
        self._cache[key] = list(value)
        self._do('--replace-all', name, value[0])
        for i in range(1, len(value)):
          self._do('--add', name, value[i])

    elif len(old) != 1 or old[0] != value:
      self._cache[key] = [value]
      self._do('--replace-all', name, value)

  """
  返回'remote.$name.*'配置对象

  $ cat .git/.repo_config.json
  {
    ...
    "remote.origin.url": [
      "https://gerrit.googlesource.com/git-repo"
    ],
    ...
    "remote.origin.fetch": [
      "+refs/heads/*:refs/remotes/origin/*"
    ]
  }
  """
  def GetRemote(self, name):
    """Get the remote.$name.* configuration values as an object.
    """
    try:
      r = self._remotes[name]
    except KeyError:
      r = Remote(self, name)
      self._remotes[r.name] = r
    return r

  """
  返回'branch.$name.*'配置对象，如：GetBranch("default")

  $ cat .git/.repo_config.json
  {
    ...
    "branch.default.merge": [
      "refs/heads/stable"
    ],
    "branch.default.remote": [
      "origin"
    ],
    ...
  }
  """
  def GetBranch(self, name):
    """Get the branch.$name.* configuration values as an object.
    """
    try:
      b = self._branches[name]
    except KeyError:
      b = Branch(self, name)
      self._branches[b.name] = b
    return b

  def GetSubSections(self, section):
    """List all subsection names matching $section.*.*
    """
    return self._sections.get(section, set())

  def HasSection(self, section, subsection = ''):
    """Does at least one key in section.subsection exist?
    """
    try:
      return subsection in self._sections[section]
    except KeyError:
      return False

  """
  检查配置文件中'url.*.insteadof'选项，并对地址进行转换

  如：
  'https://gerrit.googlesource.com/git-repo' 通过UrlInsteadOf()被转换为：
  --> 'http://localhost/mirror/git-repo'
  """
  def UrlInsteadOf(self, url):
    """Resolve any url.*.insteadof references.
    """
    """
    config文件中 'url.*.insteadof' 节的参考示例：
    $ cat .git/config
    ...
    [url "http://localhost/mirror"]
      insteadof = https://gerrit.googlesource.com
      insteadof = https://github.com/guyongqiangx
      insteadof = https://aosp.tuna.tsinghua.edu.cn
    ...

    这里解析的结果：
    new_url = 'http://localhost/mirror'
    old_url = [ 'https://gerrit.googlesource.com',
                'https://github.com/guyongqiangx',
                'https://aosp.tuna.tsinghua.edu.cn' ]

    如果传入的url以old_url列表中的某个地址开始，则使用new_url对old_url这部分进行替换，如：
    url = 'https://gerrit.googlesource.com/git-repo'
    显然，url地址以old_url列表的第一项开始，所以需要用new_url进行替换。

    因此，地址：
    'https://gerrit.googlesource.com/git-repo' 通过UrlInsteadOf()被转换为：
    --> 'http://localhost/mirror/git-repo'
    """
    for new_url in self.GetSubSections('url'):
      for old_url in self.GetString('url.%s.insteadof' % new_url, True):
        if old_url is not None and url.startswith(old_url):
          return new_url + url[len(old_url):]
    return url

  """
  返回'.git/.repo_config.json'中的所有sections

  访问_sections属性会加载'.git/.repo_config.json'中的键值对数据的section部分到_section_dict字典成员中，并返回
  """
  @property
  def _sections(self):
    """
    访问_section_dict字典成员
    如果为空，则遍历_cache字典成员的所有key，并使用'.'进行分割。
    """
    d = self._section_dict
    if d is None:
      d = {}
      for name in self._cache.keys():
        p = name.split('.')
        if 2 == len(p):
          section = p[0]
          subsect = ''
        else:
          section = p[0]
          subsect = '.'.join(p[1:-1])
        if section not in d:
          d[section] = set()
        d[section].add(subsect)
        self._section_dict = d
    return d

  """
  '._cache'属性返回'.git/.repo_config.json'中的所有键值对。

  访问_cache属性会加载'.git/.repo_config.json'中的键值对数据，存放到_cache_dict字典成员中，并返回
  """
  @property
  def _cache(self):
    if self._cache_dict is None:
      self._cache_dict = self._Read()
    return self._cache_dict

  """
  读取'.git/.repo_config.json'中的键值对数据

  如果没有读取到'.git/.repo_config.json'文件，
  则加载'configfile'中的数据，并保存到'.git/.repo_config.json'文件中
  """
  def _Read(self):
    d = self._ReadJson()
    if d is None:
      d = self._ReadGit()
      self._SaveJson(d)
    return d

  """
  加载'.git/.repo_config.json'中的键值对数据
  """
  def _ReadJson(self):
    """
    比较'.git/.repo_config.json'和'.git/config'文件的时间戳,
    如果_json比.git/config文件旧，则发生了错误，因为每次同步都会更新_json文件，肯定比'.git/config'文件新

    加载_json文件中的键值对，如：
    .repo/repo$ cat .git/.repo_config.json
    {
      ...
      "remote.origin.url": [
        "https://gerrit.googlesource.com/git-repo"
      ],
      "branch.default.merge": [
        "refs/heads/stable"
      ],
      "branch.default.remote": [
        "origin"
      ],
      "remote.origin.fetch": [
        "+refs/heads/*:refs/remotes/origin/*"
      ]
    }
    """
    try:
      if os.path.getmtime(self._json) \
      <= os.path.getmtime(self.file):
        os.remove(self._json)
        return None
    except OSError:
      return None
    try:
      Trace(': parsing %s', self.file)
      fd = open(self._json)
      try:
        return json.load(fd)
      finally:
        fd.close()
    except (IOError, ValueError):
      os.remove(self._json)
      return None

  """
  保存cache中的数据到'.git/.repo_config.json'文件中
  """
  def _SaveJson(self, cache):
    try:
      fd = open(self._json, 'w')
      try:
        json.dump(cache, fd, indent=2)
      finally:
        fd.close()
    except (IOError, TypeError):
      if os.path.exists(self._json):
        os.remove(self._json)

  """
  读取config文件的键值对，并以字典的方式返回

  具体是读取用户级别的'~/.gitconfig'还是仓库级别的'.git/config'，则由初始化时指定的configfile决定。
  """
  def _ReadGit(self):
    """
    Read configuration data from git.

    This internal method populates the GitConfig cache.

    """
    c = {}
    """
    _do调用会执行命令：'git config --file file --null --list'
    用于列举'.git/config'或'~/.gitconfig'中的设置，如：
    $ cat .git/config
    [core]
      repositoryformatversion = 0
      filemode = true
      bare = false
      logallrefupdates = true
    [remote "origin"]
      url = https://gerrit.googlesource.com/git-repo
      fetch = +refs/heads/*:refs/remotes/origin/*
    [branch "default"]
      remote = origin
      merge = refs/heads/stable
    $ git config --file .git/config --null --list | hexdump -Cv
    00000000  63 6f 72 65 2e 72 65 70  6f 73 69 74 6f 72 79 66  |core.repositoryf|
    00000010  6f 72 6d 61 74 76 65 72  73 69 6f 6e 0a 30 00 63  |ormatversion.0.c|
    00000020  6f 72 65 2e 66 69 6c 65  6d 6f 64 65 0a 74 72 75  |ore.filemode.tru|
    00000030  65 00 63 6f 72 65 2e 62  61 72 65 0a 66 61 6c 73  |e.core.bare.fals|
    00000040  65 00 63 6f 72 65 2e 6c  6f 67 61 6c 6c 72 65 66  |e.core.logallref|
    00000050  75 70 64 61 74 65 73 0a  74 72 75 65 00 72 65 6d  |updates.true.rem|
    00000060  6f 74 65 2e 6f 72 69 67  69 6e 2e 75 72 6c 0a 68  |ote.origin.url.h|
    00000070  74 74 70 73 3a 2f 2f 67  65 72 72 69 74 2e 67 6f  |ttps://gerrit.go|
    00000080  6f 67 6c 65 73 6f 75 72  63 65 2e 63 6f 6d 2f 67  |oglesource.com/g|
    00000090  69 74 2d 72 65 70 6f 00  72 65 6d 6f 74 65 2e 6f  |it-repo.remote.o|
    000000a0  72 69 67 69 6e 2e 66 65  74 63 68 0a 2b 72 65 66  |rigin.fetch.+ref|
    000000b0  73 2f 68 65 61 64 73 2f  2a 3a 72 65 66 73 2f 72  |s/heads/*:refs/r|
    000000c0  65 6d 6f 74 65 73 2f 6f  72 69 67 69 6e 2f 2a 00  |emotes/origin/*.|
    000000d0  62 72 61 6e 63 68 2e 64  65 66 61 75 6c 74 2e 72  |branch.default.r|
    000000e0  65 6d 6f 74 65 0a 6f 72  69 67 69 6e 00 62 72 61  |emote.origin.bra|
    000000f0  6e 63 68 2e 64 65 66 61  75 6c 74 2e 6d 65 72 67  |nch.default.merg|
    00000100  65 0a 72 65 66 73 2f 68  65 61 64 73 2f 73 74 61  |e.refs/heads/sta|
    00000110  62 6c 65 00                                       |ble.|
    00000114

    这里由于使用'--null'选项，所以得到的键值对是使用'\0'来分割的，文本模式无法识别，只能通过十六进制查看。

    - 使用'\0'(即null)来分割两项间的设置
    - 使用'\n'(即0x0a)来分割每一项的键值对(key, value)

    最后将分割得到的键值对(key, value)存放到c列表中。
    """
    d = self._do('--null', '--list')
    if d is None:
      return c
    for line in d.decode('utf-8').rstrip('\0').split('\0'):  # pylint: disable=W1401
                                                             # Backslash is not anomalous
      if '\n' in line:
        key, val = line.split('\n', 1)
      else:
        key = line
        val = None

      if key in c:
        c[key].append(val)
      else:
        c[key] = [val]

    return c

  """
  保存args中的设置到用户级别或仓库级别的config文件中。
  """
  def _do(self, *args):
    """
    构造命令：'git config --file file key value'

    这里的file可能是用户级别的'~/.gitconfig'或仓库级别的'.git/config':

    如：
    'git config --file ~/.gitconfig user.name guyongqiangx'
    'git config --file .git/config user.name guyongqiangx'
    """
    command = ['config', '--file', self.file]
    command.extend(args)

    p = GitCommand(None,
                   command,
                   capture_stdout = True,
                   capture_stderr = True)
    if p.Wait() == 0:
      return p.stdout
    else:
      GitError('git config %s: %s' % (str(args), p.stderr))

"""
一个RefSpec对象代表config文件中的'remote.$name.fetch'属性设置, 如：
$ cat .git/config
...
[remote "origin"]
  url = https://gerrit.googlesource.com/git-repo
  fetch = +refs/heads/*:refs/remotes/origin/*
...
其fetch属性指定了远程分支和本地分支的对应关系，即从远程抓取的各分支对象应该更新到本地的哪些分支上
"""
class RefSpec(object):
  """
  '.git/config'文件中，'remote.$name.fetch'属性指定了fetch操作的refspec
  换句话说，指定了名为$name的远程源和本地分支的对应关系，如:
  $ cat .git/config
  ...
  [remote "origin"]
    url = https://gerrit.googlesource.com/git-repo
    fetch = +refs/heads/*:refs/remotes/origin/*

  其refspec为'+refs/heads/*:refs/remotes/origin/*'
  即抓取远程'remote'的分支('refs/heads/*')数据，更新到本地分支('refs/remotes/origin/*')下。

  因此，
  如果执行'git fetch'命令，默认会拉取'remote'源所有的分支('refs/heads/*')，更新到相应的跟踪分支('refs/remotes/origin/*')上。
  ...
  """

  """A Git refspec line, split into its components:

      forced:  True if the line starts with '+'
      src:     Left side of the line
      dst:     Right side of the line
  """

  """
  使用类似'+refs/heads/*:refs/remotes/origin/*'字符串初始化RefSpec类对象。
  如 rs = '+refs/heads/*:refs/remotes/origin/*'，有:
     lhs = 'refs/heads/*'
     rhl = 'refs/remotes/origin/*'
  由于rs以'+'开始，所以 forced = True
  """
  @classmethod
  def FromString(cls, rs):
    lhs, rhs = rs.split(':', 2)
    if lhs.startswith('+'):
      lhs = lhs[1:]
      forced = True
    else:
      forced = False
    return cls(forced, lhs, rhs)

  """
  构造函数

  例如：RefSpec(True, 'refs/heads/*', dst)
  """
  def __init__(self, forced, lhs, rhs):
    self.forced = forced
    self.src = lhs
    self.dst = rhs

  """
  判断rev指定的分支是否包含在src分支规则指定的分支中

  如src='refs/heads/*', rev='refs/heads/master'
  src指明所有以'refs/heads/'开头的分支，rev显然满足。
  """
  def SourceMatches(self, rev):
    """
    Match的条件：
    1. src和rev相同
    2. 或src以'/*'结尾且rev包含src除'*'外的字符串
       如src='refs/heads/*', rev='refs/heads/master'
    """
    if self.src:
      if rev == self.src:
        return True
      if self.src.endswith('/*') and rev.startswith(self.src[:-1]):
        return True
    return False

  """
  判断rev指定的分支是否包含在dst分支规则指定的分支中

  如dst='refs/heads/*', rev='refs/heads/master'
  dst指明所有以'refs/heads/'开头的分支，rev显然满足。
  """
  def DestMatches(self, ref):
    """
    Match的条件:
    1. ref和dst相同
    2. 或dst以'/*'结尾且ref包含dst除'*'外的字符串
       如dst='refs/heads/*', ref='refs/heads/master'
    """
    if self.dst:
      if ref == self.dst:
        return True
      if self.dst.endswith('/*') and ref.startswith(self.dst[:-1]):
        return True
    return False

  """
  返回与rev匹配的dst分支
  """
  def MapSource(self, rev):
    """
    如果src匹配所有分支(以'/*'结尾)，则返回rev对应的具体的目标分支, 如:
    src = 'refs/heads/*'
    dst = 'refs/remotes/origin/*'

    调用MapSource(rev = 'refs/heads/stable')返回与rev匹配的dst分支为：'refs/remotes/origin/stable'
    """
    if self.src.endswith('/*'):
      return self.dst[:-1] + rev[len(self.src) - 1:]
    return self.dst

  """
  将RefSpec对象转换为字符串'+src:dst'的格式

  如：'+refs/heads/*:refs/remotes/origin/*'
  """
  def __str__(self):
    s = ''
    if self.forced:
      s += '+'
    if self.src:
      s += self.src
    if self.dst:
      s += ':'
      s += self.dst
    return s


_master_processes = []
_master_keys = set()
_ssh_master = True
_master_keys_lock = None

def init_ssh():
  """Should be called once at the start of repo to init ssh master handling.

  At the moment, all we do is to create our lock.
  """
  global _master_keys_lock
  assert _master_keys_lock is None, "Should only call init_ssh once"
  _master_keys_lock = _threading.Lock()

def _open_ssh(host, port=None):
  global _ssh_master

  # Acquire the lock.  This is needed to prevent opening multiple masters for
  # the same host when we're running "repo sync -jN" (for N > 1) _and_ the
  # manifest <remote fetch="ssh://xyz"> specifies a different host from the
  # one that was passed to repo init.
  _master_keys_lock.acquire()
  try:

    # Check to see whether we already think that the master is running; if we
    # think it's already running, return right away.
    if port is not None:
      key = '%s:%s' % (host, port)
    else:
      key = host

    if key in _master_keys:
      return True

    if not _ssh_master \
    or 'GIT_SSH' in os.environ \
    or sys.platform in ('win32', 'cygwin'):
      # failed earlier, or cygwin ssh can't do this
      #
      return False

    # We will make two calls to ssh; this is the common part of both calls.
    command_base = ['ssh',
                     '-o','ControlPath %s' % ssh_sock(),
                     host]
    if port is not None:
      command_base[1:1] = ['-p', str(port)]

    # Since the key wasn't in _master_keys, we think that master isn't running.
    # ...but before actually starting a master, we'll double-check.  This can
    # be important because we can't tell that that 'git@myhost.com' is the same
    # as 'myhost.com' where "User git" is setup in the user's ~/.ssh/config file.
    check_command = command_base + ['-O','check']
    try:
      Trace(': %s', ' '.join(check_command))
      check_process = subprocess.Popen(check_command,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
      check_process.communicate() # read output, but ignore it...
      isnt_running = check_process.wait()

      if not isnt_running:
        # Our double-check found that the master _was_ infact running.  Add to
        # the list of keys.
        _master_keys.add(key)
        return True
    except Exception:
      # Ignore excpetions.  We we will fall back to the normal command and print
      # to the log there.
      pass

    command = command_base[:1] + \
              ['-M', '-N'] + \
              command_base[1:]
    try:
      Trace(': %s', ' '.join(command))
      p = subprocess.Popen(command)
    except Exception as e:
      _ssh_master = False
      print('\nwarn: cannot enable ssh control master for %s:%s\n%s'
             % (host,port, str(e)), file=sys.stderr)
      return False

    time.sleep(1)
    ssh_died = (p.poll() is not None)
    if ssh_died:
      return False

    _master_processes.append(p)
    _master_keys.add(key)
    return True
  finally:
    _master_keys_lock.release()

def close_ssh():
  global _master_keys_lock

  terminate_ssh_clients()

  for p in _master_processes:
    try:
      os.kill(p.pid, SIGTERM)
      p.wait()
    except OSError:
      pass
  del _master_processes[:]
  _master_keys.clear()

  d = ssh_sock(create=False)
  if d:
    try:
      os.rmdir(os.path.dirname(d))
    except OSError:
      pass

  # We're done with the lock, so we can delete it.
  _master_keys_lock = None

URI_SCP = re.compile(r'^([^@:]*@?[^:/]{1,}):')
URI_ALL = re.compile(r'^([a-z][a-z+-]*)://([^@/]*@?[^/]*)/')

def GetSchemeFromUrl(url):
  m = URI_ALL.match(url)
  if m:
    return m.group(1)
  return None

@contextlib.contextmanager
def GetUrlCookieFile(url, quiet):
  if url.startswith('persistent-'):
    try:
      p = subprocess.Popen(
          ['git-remote-persistent-https', '-print_config', url],
          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
          stderr=subprocess.PIPE)
      try:
        cookieprefix = 'http.cookiefile='
        proxyprefix = 'http.proxy='
        cookiefile = None
        proxy = None
        for line in p.stdout:
          line = line.strip()
          if line.startswith(cookieprefix):
            cookiefile = line[len(cookieprefix):]
          if line.startswith(proxyprefix):
            proxy = line[len(proxyprefix):]
        # Leave subprocess open, as cookie file may be transient.
        if cookiefile or proxy:
          yield cookiefile, proxy
          return
      finally:
        p.stdin.close()
        if p.wait():
          err_msg = p.stderr.read()
          if ' -print_config' in err_msg:
            pass  # Persistent proxy doesn't support -print_config.
          elif not quiet:
            print(err_msg, file=sys.stderr)
    except OSError as e:
      if e.errno == errno.ENOENT:
        pass  # No persistent proxy.
      raise
  yield GitConfig.ForUser().GetString('http.cookiefile'), None

def _preconnect(url):
  m = URI_ALL.match(url)
  if m:
    scheme = m.group(1)
    host = m.group(2)
    if ':' in host:
      host, port = host.split(':')
    else:
      port = None
    if scheme in ('ssh', 'git+ssh', 'ssh+git'):
      return _open_ssh(host, port)
    return False

  m = URI_SCP.match(url)
  if m:
    host = m.group(1)
    return _open_ssh(host)

  return False

"""
一个Remote对象代表config文件中的一个remote设置, 如：
$ cat .git/config
...
[remote "origin"]
  url = https://gerrit.googlesource.com/git-repo
  fetch = +refs/heads/*:refs/remotes/origin/*
...

例如：aosp下的device/common:
aosp/.repo/projects/device/common.git$ cat config
...
[remote "aosp"]
  url = https://aosp.tuna.tsinghua.edu.cn/device/common
  projectname = device/common
  fetch = +refs/heads/*:refs/remotes/aosp/*

一个Remote对象有8个属性，分别为：
    _config: 包含当前remote设置的config对象
       name: remote的名称
        url: remote的url属性
    pushUrl: remote的pushUrl属性
     review:
projectname:
      fetch:
_review_url:
"""
class Remote(object):
  """Configuration options related to a remote.
  """
  def __init__(self, config, name):
    self._config = config
    self.name = name
    self.url = self._Get('url')
    self.pushUrl = self._Get('pushurl')
    self.review = self._Get('review')
    self.projectname = self._Get('projectname')
    self.fetch = list(map(RefSpec.FromString,
                      self._Get('fetch', all_keys=True)))
    self._review_url = None

  """
  返回url的insteadOf地址
  """
  def _InsteadOf(self):
    """
    检查用户级配置文件'~/.gitconfig'的'url'节设置，并解析'.insteadOf'的地址列表
    """
    globCfg = GitConfig.ForUser()
    urlList = globCfg.GetSubSections('url')
    longest = ""
    longestUrl = ""

    """
    逐条检查insteadOf设置，如：
    $ cat ~/.gitconfig
    ...
    [url "http://localhost"]
      insteadof = https://gerrit.googlesource.com/git-repo
      insteadof = https://github.com/guyongqiangx/git-repo
      insteadof = https://aosp.tuna.tsinghua.edu.cn/git-repo

    insteadOfList包含所有的'insteadof'结果。
    """
    for url in urlList:
      key = "url." + url + ".insteadOf"
      insteadOfList = globCfg.GetString(key, all_keys=True)

      for insteadOf in insteadOfList:
        if self.url.startswith(insteadOf) \
        and len(insteadOf) > len(longest):
          longest = insteadOf
          longestUrl = url

    if len(longest) == 0:
      return self.url

    return self.url.replace(longest, longestUrl, 1)

  def PreConnectFetch(self):
    connectionUrl = self._InsteadOf()
    return _preconnect(connectionUrl)

  def ReviewUrl(self, userEmail):
    if self._review_url is None:
      if self.review is None:
        return None

      u = self.review
      if u.startswith('persistent-'):
        u = u[len('persistent-'):]
      if u.split(':')[0] not in ('http', 'https', 'sso'):
        u = 'http://%s' % u
      if u.endswith('/Gerrit'):
        u = u[:len(u) - len('/Gerrit')]
      if u.endswith('/ssh_info'):
        u = u[:len(u) - len('/ssh_info')]
      if not u.endswith('/'):
        u += '/'
      http_url = u

      if u in REVIEW_CACHE:
        self._review_url = REVIEW_CACHE[u]
      elif 'REPO_HOST_PORT_INFO' in os.environ:
        host, port = os.environ['REPO_HOST_PORT_INFO'].split()
        self._review_url = self._SshReviewUrl(userEmail, host, port)
        REVIEW_CACHE[u] = self._review_url
      elif u.startswith('sso:'):
        self._review_url = u  # Assume it's right
        REVIEW_CACHE[u] = self._review_url
      else:
        try:
          info_url = u + 'ssh_info'
          info = urllib.request.urlopen(info_url).read()
          if info == 'NOT_AVAILABLE' or '<' in info:
            # If `info` contains '<', we assume the server gave us some sort
            # of HTML response back, like maybe a login page.
            #
            # Assume HTTP if SSH is not enabled or ssh_info doesn't look right.
            self._review_url = http_url
          else:
            host, port = info.split()
            self._review_url = self._SshReviewUrl(userEmail, host, port)
        except urllib.error.HTTPError as e:
          raise UploadError('%s: %s' % (self.review, str(e)))
        except urllib.error.URLError as e:
          raise UploadError('%s: %s' % (self.review, str(e)))
        except HTTPException as e:
          raise UploadError('%s: %s' % (self.review, e.__class__.__name__))

        REVIEW_CACHE[u] = self._review_url
    return self._review_url + self.projectname

  def _SshReviewUrl(self, userEmail, host, port):
    username = self._config.GetString('review.%s.username' % self.review)
    if username is None:
      username = userEmail.split('@')[0]
    return 'ssh://%s@%s:%s/' % (username, host, port)

  def ToLocal(self, rev):
    """Convert a remote revision string to something we have locally.
    """
    if self.name == '.' or IsId(rev):
      return rev

    if not rev.startswith('refs/'):
      rev = R_HEADS + rev

    for spec in self.fetch:
      if spec.SourceMatches(rev):
        return spec.MapSource(rev)

    if not rev.startswith(R_HEADS):
      return rev

    raise GitError('remote %s does not have %s' % (self.name, rev))

  def WritesTo(self, ref):
    """True if the remote stores to the tracking ref.
    """
    for spec in self.fetch:
      if spec.DestMatches(ref):
        return True
    return False

  """
  根据mirror设置，更新RefSpec对象的映射信息，即远程分支和本地分支的对应关系
  """
  def ResetFetch(self, mirror=False):
    """Set the fetch refspec to its default value.
    """
    """
    根据是否基于mirror镜像，构建不同的refspec:
    1. 基于mirror镜像
       fetch = '+refs/heads/*:refs/heads/*'
    2. 不基于mirror镜像
       fetch = '+refs/heads/*:refs/remotes/origin/*'
    """
    if mirror:
      dst = 'refs/heads/*'
    else:
      dst = 'refs/remotes/%s/*' % self.name
    self.fetch = [RefSpec(True, 'refs/heads/*', dst)]

  """
  将remote的设置保存到当前config对应的文件中

  保存的设置包括：
  $ cat .git/config
  ...
  [remote "origin"]
    url = https://github.com/guyongqiangx/git-repo.git
    pushurl = ...
    review = ...
    projectname = ...
    fetch = +refs/heads/*:refs/remotes/origin/*
  ...
  """
  def Save(self):
    """Save this remote to the configuration.
    """
    self._Set('url', self.url)
    if self.pushUrl is not None:
      self._Set('pushurl', self.pushUrl + '/' + self.projectname)
    else:
      self._Set('pushurl', self.pushUrl)
    self._Set('review', self.review)
    self._Set('projectname', self.projectname)
    self._Set('fetch', list(map(str, self.fetch)))

  """
  使用value设置当前config中指定remote的key项
  remote.$name.$key = $value

  $ cat .git/config
  ...
  [remote "$name"]
    $key = $value
  """
  def _Set(self, key, value):
    """
    构造并执行命令：
    'git config --file file remote.$name.$key $value'
    如：
    ''
    """
    key = 'remote.%s.%s' % (self.name, key)
    return self._config.SetString(key, value)

  """
  获取当前config指定remote的key设置
  remote.$name.$key

  $ cat .git/config
  ...
  [remote "$name"]
    $key = $value
  """
  def _Get(self, key, all_keys=False):
    key = 'remote.%s.%s' % (self.name, key)
    return self._config.GetString(key, all_keys = all_keys)

"""
一个Branch对象代表config文件中的一个branch分支, 如：
$ cat .git/config
...
[branch "default"]
  remote = origin
  merge = refs/heads/stable
...

一个Branch对象有4个属性，分别为：
_config: 包含当前branch的config对象
   name: branch的名字
  merge: branch的merge属性
 remote: branch的remote属性
"""
class Branch(object):
  """Configuration options related to a single branch.
  """
  def __init__(self, config, name):
    self._config = config
    self.name = name
    self.merge = self._Get('merge')

    r = self._Get('remote')
    if r:
      self.remote = self._config.GetRemote(r)
    else:
      self.remote = None

  @property
  def LocalMerge(self):
    """Convert the merge spec to a local name.
    """
    if self.remote and self.merge:
      return self.remote.ToLocal(self.merge)
    return None

  """
  将branch的设置(remote和merge)保存到当前config对应的文件中
  """
  def Save(self):
    """Save this branch back into the configuration.
    """
    if self._config.HasSection('branch', self.name):
      if self.remote:
        self._Set('remote', self.remote.name)
      else:
        self._Set('remote', None)
      self._Set('merge', self.merge)

    else:
      fd = open(self._config.file, 'a')
      try:
        fd.write('[branch "%s"]\n' % self.name)
        if self.remote:
          fd.write('\tremote = %s\n' % self.remote.name)
        if self.merge:
          fd.write('\tmerge = %s\n' % self.merge)
      finally:
        fd.close()

  """
  使用value设置当前config指定branch的key项
  branch.$name.$key = $value
  """
  def _Set(self, key, value):
    key = 'branch.%s.%s' % (self.name, key)
    return self._config.SetString(key, value)

  """
  获取当前config指定branch的key设置
  branch.$name.$key
  """
  def _Get(self, key, all_keys=False):
    key = 'branch.%s.%s' % (self.name, key)
    return self._config.GetString(key, all_keys = all_keys)
