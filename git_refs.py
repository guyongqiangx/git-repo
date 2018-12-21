# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 The Android Open Source Project
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

import os
from trace import Trace

HEAD    = 'HEAD'
R_HEADS = 'refs/heads/'
R_TAGS  = 'refs/tags/'
R_PUB   = 'refs/published/'
R_M     = 'refs/remotes/m/'


"""
管理指定gitdir目录下的所有引用
"""
class GitRefs(object):
  def __init__(self, gitdir):
    """
    _gitdir: 指向'.git'目录
    _phyref: 基于分支引用和commit id键值对的字典
             如: _phyref['refs/remotes/origin/master'] = 'c00d28...15'
    _symref: 基于分支引用间的键值对字典
             如：_symref[HEAD] = 'refs/heads/stable'
     _mtime: 某个分支引用文件的更新时间
             如：_mtime['refs/remotes/origin/master'] = os.path.getmtime('.git/refs/remotes/origin/master')
    """
    self._gitdir = gitdir
    self._phyref = None
    self._symref = None
    self._mtime = {}

  """
  返回包含所有引用的字典 _phyref
  """
  @property
  def all(self):
    self._EnsureLoaded()
    return self._phyref

  """
  返回名称为'name'的引用对应的提交id
  """
  def get(self, name):
    try:
      return self.all[name]
    except KeyError:
      return ''

  """
  删除名称为'name'的引用的所有信息，包括提交id，引用符号名以及更新的时间
  """
  def deleted(self, name):
    if self._phyref is not None:
      if name in self._phyref:
        del self._phyref[name]

      if name in self._symref:
        del self._symref[name]

      if name in self._mtime:
        del self._mtime[name]

  """
  返回符号引用对应的名称

  如：'_symref[name]' = 'refs/heads/stable'
  """
  def symref(self, name):
    try:
      self._EnsureLoaded()
      return self._symref[name]
    except KeyError:
      return ''

  """
  确保当前引用字典已经更新，如果需要更新，则检查所有的引用并进行更新
  """
  def _EnsureLoaded(self):
    if self._phyref is None or self._NeedUpdate():
      self._LoadAll()

  """
  检查引用是否需要更新

  读取所有引用文件更新的时间字典_mtime，和当前文件的时间进行对比，
  如果文件时间较新，说明需要更新引用。
  """
  def _NeedUpdate(self):
    Trace(': scan refs %s', self._gitdir)

    for name, mtime in self._mtime.items():
      try:
        if mtime != os.path.getmtime(os.path.join(self._gitdir, name)):
          return True
      except OSError:
        return True
    return False

  """
  加载'.git'目录下的所有引用，并更新引用字典和引用文件最后更新的时间戳

  查找的引用包括：
  1. '.git/packed-refs'文件
  2. 遍历'.git/refs/'下的引用文件
  3. '.git/HEAD'文件
  """
  def _LoadAll(self):
    Trace(': load refs %s', self._gitdir)

    self._phyref = {}
    self._symref = {}
    self._mtime = {}

    """
    使用以下文件来建立引用字典和引用文件最后更新的时间戳

    读取'.git/packed-refs'文件
    遍历'.git/refs/'下的引用文件
    读取'.git/HEAD'文件
    """
    self._ReadPackedRefs()
    self._ReadLoose('refs/')
    self._ReadLoose1(os.path.join(self._gitdir, HEAD), HEAD)

    scan = self._symref
    attempts = 0
    while scan and attempts < 5:
      scan_next = {}
      for name, dest in scan.items():
        if dest in self._phyref:
          self._phyref[name] = self._phyref[dest]
        else:
          scan_next[name] = dest
      scan = scan_next
      attempts += 1

  """
  读取'.git/packed-refs'文件，构建由引用名和提交id键值对组成的字典: _phyref[name] = ref_id
  """
  def _ReadPackedRefs(self):
    """
    读取'.git/packed-refs'文件，忽略其中'#'和'^'开头的行

    $ cat packed-refs
    # pack-refs with: peeled fully-peeled
    a17df4e9905003362b245d8a78c6f34071d327a7 refs/remotes/origin/guyongqiangx
    34acdd253439448b6c08c3abfc5e7b8bd03f383f refs/remotes/origin/maint
    c00d28b767240ef17a0402a7d55a7a6197ce2815 refs/remotes/origin/master
    eceeb1b1f5edb0f42e690bffdf81828abd8ea7fe refs/remotes/origin/stable
    adf9c1dabb015d83af63b300b3d4cb97d7cf41ba refs/tags/v1.0
    ^cf31fe9b4fb650b27e19f5d7ee7297e383660caf
    0d6646b94f090e76b6d635613d9164f53e770255 refs/tags/v1.0.1
    ^7542d664de7a9d42f64a81bc8c0b86bcbb384376
    e2e361af160d861e4d9f73a6ba0913c7e17735a4 refs/tags/v1.0.2
    ^02dbb6d120e44ec22cc7051251984cfd618e74ce

    将正常的行进行分割，得到ref_id和name项，并存放到_phyref[name] = ref_id字典中。
    如： c00d28b767240ef17a0402a7d55a7a6197ce2815 refs/remotes/origin/master
    有： _phyref['refs/remotes/origin/master'] = 'c00d28b767240ef17a0402a7d55a7a6197ce2815'
    """
    path = os.path.join(self._gitdir, 'packed-refs')
    try:
      fd = open(path, 'r')
      mtime = os.path.getmtime(path)
    except IOError:
      return
    except OSError:
      return
    try:
      for line in fd:
        line = str(line)
        if line[0] == '#':
          continue
        if line[0] == '^':
          continue

        line = line[:-1]
        p = line.split(' ')
        ref_id = p[0]
        name = p[1]

        self._phyref[name] = ref_id
    finally:
      fd.close()
    self._mtime['packed-refs'] = mtime

  """
  递归列举 '.git/refs/'下的所有引用文件，并更新引用字典和引用对应的时间戳
  """
  def _ReadLoose(self, prefix):
    """
    例如：_ReadLoose('refs/')
    递归列举 '.git/refs/'下的所有引用文件，并更新引用字典。

    $ tree .git/refs/
    .git/refs/
    ├── heads
    │   ├── comments
    │   ├── guyongqiangx
    │   └── stable
    ├── remotes
    │   └── origin
    │       ├── comments
    │       └── HEAD
    └── tags

    4 directories, 5 files
    """
    base = os.path.join(self._gitdir, prefix)
    for name in os.listdir(base):
      p = os.path.join(base, name)
      if os.path.isdir(p):
        self._mtime[prefix] = os.path.getmtime(base)
        self._ReadLoose(prefix + name + '/')
      elif name.endswith('.lock'):
        pass
      else:
        self._ReadLoose1(p, prefix + name)

  """
  读取path指定文件的内容得到ref_id，然后和name组成键值对添加到 _symref[name] = ref_id 或 _phyref[name] = ref_id

  指向分支的引用，如'refs/heads/stable'，则更新_symref[name] = ref_id
  指向具体的提交id，如'fa2ff859..ee'，则更新_phyref[name] = ref_id

  同时更新引用时间戳字典 _mtime[name] = mtime
  """
  def _ReadLoose1(self, path, name):
    """
    例如：_ReadLoose1('.git/HEAD', HEAD)
    有两种情况：
    1. 指向某个分支引用
    $ cat .git/HEAD
    ref: refs/heads/stable

    存放到字典：_symref[HEAD] = 'refs/heads/stable'

    2. 指针分离状态下指向某个具体的提交对象
    $ git update-ref --no-deref HEAD fa2ff85
    $ git status
    HEAD detached from 2ea37bc
    nothing to commit, working directory clean
    $ cat .git/HEAD
    fa2ff85933d90139bf0340bd6dda4331effbe4ee

    存放到字典：_phyref[HEAD] = 'fa2ff85933d90139bf0340bd6dda4331effbe4ee'
    """
    try:
      fd = open(path, 'rb')
    except IOError:
      return

    try:
      try:
        mtime = os.path.getmtime(path)
        ref_id = fd.readline()
      except (IOError, OSError):
        return
    finally:
      fd.close()

    try:
      ref_id = ref_id.decode()
    except AttributeError:
      pass
    if not ref_id:
      return
    ref_id = ref_id[:-1]

    if ref_id.startswith('ref: '):
      self._symref[name] = ref_id[5:]
    else:
      self._phyref[name] = ref_id
    self._mtime[name] = mtime
