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
import itertools
import os
import re
import sys
import xml.dom.minidom

from pyversion import is_python3
if is_python3():
  import urllib.parse
else:
  import imp
  import urlparse
  urllib = imp.new_module('urllib')
  urllib.parse = urlparse

import gitc_utils
from git_config import GitConfig
from git_refs import R_HEADS, HEAD
from project import RemoteSpec, Project, MetaProject
from error import ManifestParseError, ManifestInvalidRevisionError

MANIFEST_FILE_NAME = 'manifest.xml'
LOCAL_MANIFEST_NAME = 'local_manifest.xml'
LOCAL_MANIFESTS_DIR_NAME = 'local_manifests'

# urljoin gets confused if the scheme is not known.
urllib.parse.uses_relative.extend(['ssh', 'git', 'persistent-https', 'rpc'])
urllib.parse.uses_netloc.extend(['ssh', 'git', 'persistent-https', 'rpc'])

"""
_Default类对象
"""
class _Default(object):
  """Project defaults within the manifest."""

  revisionExpr = None
  destBranchExpr = None
  remote = None
  sync_j = 1
  sync_c = False
  sync_s = False

  """
  运算符重载

  如果两个对象的__dict__成员列表值一样，说明二者相同
  """
  def __eq__(self, other):
    return self.__dict__ == other.__dict__

  """
  运算符重载

  如果两个对象的__dict__成员列表值不一样，说明二者不同
  """
  def __ne__(self, other):
    return self.__dict__ != other.__dict__

"""
_XmlRemote对象
"""
class _XmlRemote(object):
  """
  初始化类对象，成员包括：
  .name
  .fetchUrl
  .pushUrl
  .manifestUrl
  .remoteAlias
  .reviewUrl
  .revision
  .resolvedFetchUrl
  """
  def __init__(self,
               name,
               alias=None,
               fetch=None,
               pushUrl=None,
               manifestUrl=None,
               review=None,
               revision=None):
    self.name = name
    self.fetchUrl = fetch
    self.pushUrl = pushUrl
    self.manifestUrl = manifestUrl
    self.remoteAlias = alias
    self.reviewUrl = review
    self.revision = revision
    self.resolvedFetchUrl = self._resolveFetchUrl()

  def __eq__(self, other):
    return self.__dict__ == other.__dict__

  def __ne__(self, other):
    return self.__dict__ != other.__dict__

  """
  使用fetchUrl或menifestUrl来构建resolvedFetchUrl
  """
  def _resolveFetchUrl(self):
    url = self.fetchUrl.rstrip('/')
    manifestUrl = self.manifestUrl.rstrip('/')
    # urljoin will gets confused over quite a few things.  The ones we care
    # about here are:
    # * no scheme in the base url, like <hostname:port>
    # We handle no scheme by replacing it with an obscure protocol, gopher
    # and then replacing it with the original when we are done.

    if manifestUrl.find(':') != manifestUrl.find('/') - 1:
      url = urllib.parse.urljoin('gopher://' + manifestUrl, url)
      url = re.sub(r'^gopher://', '', url)
    else:
      url = urllib.parse.urljoin(manifestUrl, url)
    return url

  """
  将projectName对应的resolvedFetchUrl转换为RemoteSpec类对象
  如:
  """
  def ToRemoteSpec(self, projectName):
    url = self.resolvedFetchUrl.rstrip('/') + '/' + projectName
    remoteName = self.name
    if self.remoteAlias:
      remoteName = self.remoteAlias
    return RemoteSpec(remoteName,
                      url=url,
                      pushUrl=self.pushUrl,
                      review=self.reviewUrl,
                      orig_name=self.name)

"""
XmlManifest对象，用于访问和操作'.git/manifest.xml'文件，其公开的接口包括：
构造函数:
  XmlManifest(repodir)
成员变量:
  repodir
  topdir
  manifestFile
  globalConfig
  localManifestWarning
  isGitcClient
  repoProject
  manifestProject
成员函数:
  Override(name)
  Link(name)
  Save(fd, peg_rev=False, peg_rev_upstream=True, groups=None)
  paths()
  projects()
  remotes()
  default()
  repo_hooks_project()
  notice()
  manifest_server()
  IsMirror()
  IsArchive()
  GetProjectPaths(name, path)
  GetProjectsWithName(name)
  GetSubprojectName(parent, submodule_path)
  GetSubprojectPaths(parent, name, path)
  projectsDiff(manifest)
"""
class XmlManifest(object):
  """manages the repo configuration file"""

  """
  使用传入'.repo'(如: '/path/to/test/.repo')的路径实例化XmlManifest类对象。
  """
  def __init__(self, repodir):
    """
    $ tree .repo -ad -L 2
    .repo
    ├── manifests
    │   └── .git
    ├── manifests.git
    │   ├── branches
    │   ├── hooks
    │   ├── info
    │   ├── logs
    │   ├── objects
    │   ├── refs
    │   ├── rr-cache
    │   └── svn
    └── repo
        ├── docs
        ├── .git
        ├── hooks
        ├── subcmds
        └── tests
    以repodir='/path/to/test/.repo'为例：

    默认初始化以下成员：
                 .repodir = '/path/to/test/.repo'
                  .topdir = '/path/to/test'
            .manifestFile = '/path/to/test/.repo/manifest.xml'
            .globalConfig = GitConfig(configfile='~/.gitconfig')
    .localManifestWarning = False
            .isGitcClient = False
             .repoProject = MetaProject(    name='repo',
                                          gitdir='/path/to/test/.repo/repo/.git',
                                        worktree='/path/to/test/.repo/repo')
         .manifestProject = MetaProject(    name='manifests',
                                          gitdir='/path/to/test/.repo/manifests.git',
                                        worktree='/path/to/test/.repo/manifests')
    """
    self.repodir = os.path.abspath(repodir)
    self.topdir = os.path.dirname(self.repodir)
    self.manifestFile = os.path.join(self.repodir, MANIFEST_FILE_NAME)
    self.globalConfig = GitConfig.ForUser()
    self.localManifestWarning = False
    self.isGitcClient = False

    """
    'repo'和'manifests'分别对应repoProject和manifestProject两个MetaProject
    """
    self.repoProject = MetaProject(self, 'repo',
      gitdir   = os.path.join(repodir, 'repo/.git'),
      worktree = os.path.join(repodir, 'repo'))

    self.manifestProject = MetaProject(self, 'manifests',
      gitdir   = os.path.join(repodir, 'manifests.git'),
      worktree = os.path.join(repodir, 'manifests'))

    """
    初始化时执行_Unload()操作清空除以上成员外的其他成员设置
    """
    self._Unload()

  """
  使用名为name的xml文件，基于该文件解析manifest信息后加载并覆盖原有信息。

  例如在'repo sync'时，如果有通过参数'-m'指定manifest_name，则使用新的manifest解析和下载。
  """
  def Override(self, name):
    """Use a different manifest, just for the current instantiation.
    """
    """
    获取名为name的文件的完整路径

    如果name='default.xml'，则有：
        path='.repo/manifests/default.xml'
    """
    path = os.path.join(self.manifestProject.worktree, name)
    if not os.path.isfile(path):
      raise ManifestParseError('manifest %s not found' % name)

    old = self.manifestFile
    try:
      self.manifestFile = path
      self._Unload()
      self._Load()
    finally:
      self.manifestFile = old

  """
  先删除'.repo/manifest.xml'，然后将'.repo/manifests/$name'文件链接到'.repo/manifest.xml'。
  """
  def Link(self, name):
    """Update the repo metadata to use a different manifest.
    """
    self.Override(name)

    try:
      """
      关于：os.path.lexists(path)
        Return True if path refers to an existing path. Returns True for broken symbolic links.

      这里不论'.repo/manifest.xml'是真实的文件还是链接文件，都先删除，然后将'manifests/$name'文件链接到'.repo/manifest.xml'。

      类似以下操作：
      $ rm -rf '/path/to/test/.repo/manifest.xml'
      $ ln -s '/path/to/test/.repo/manifests/default.xml' '/path/to/test/.repo/manifest.xml'
      """
      if os.path.lexists(self.manifestFile):
        os.remove(self.manifestFile)
      os.symlink('manifests/%s' % name, self.manifestFile)
    except OSError as e:
      raise ManifestParseError('cannot link manifest %s: %s' % (name, str(e)))

  """
  将_XmlRemote类对象转换为Xml中的remote节点
  """
  def _RemoteToXml(self, r, doc, root):
    """
    1. 在manifest的根目录下添加名为'remote'的子节点

    2. 为'remote'子节点设置'name', 'fetch', 'pushurl', 'alias', 'review'和'revision'等属性
       即: <remote name="...", fetch="..." pushurl="..." alias="..." review="..." revision="..." />
       如: <remote fetch="https://github.com" name="github"/>
    """
    e = doc.createElement('remote')
    root.appendChild(e)
    e.setAttribute('name', r.name)
    e.setAttribute('fetch', r.fetchUrl)
    if r.pushUrl is not None:
      e.setAttribute('pushurl', r.pushUrl)
    if r.remoteAlias is not None:
      e.setAttribute('alias', r.remoteAlias)
    if r.reviewUrl is not None:
      e.setAttribute('review', r.reviewUrl)
    if r.revision is not None:
      e.setAttribute('revision', r.revision)

  """
  使用逗号(',')和空白字符('\s')分割groups字符串，并返回结果列表
  """
  def _ParseGroups(self, groups):
    return [x for x in re.split(r'[,\s]+', groups) if x]

  """
  将当前manifest的内容输出到fd指定的文件中
  """
  def Save(self, fd, peg_rev=False, peg_rev_upstream=True, groups=None):
    """Write the current manifest out to the given file descriptor.
    """
    mp = self.manifestProject

    if groups is None:
      groups = mp.config.GetString('manifest.groups')
    if groups:
      groups = self._ParseGroups(groups)

    """
    生成<manifest>根节点
    """
    doc = xml.dom.minidom.Document()
    root = doc.createElement('manifest')
    doc.appendChild(root)

    """
    生成<manifest>根节点下的<notice>子节点
    """
    # Save out the notice.  There's a little bit of work here to give it the
    # right whitespace, which assumes that the notice is automatically indented
    # by 4 by minidom.
    if self.notice:
      notice_element = root.appendChild(doc.createElement('notice'))
      notice_lines = self.notice.splitlines()
      indented_notice = ('\n'.join(" "*4 + line for line in notice_lines))[4:]
      notice_element.appendChild(doc.createTextNode(indented_notice))

    d = self.default

    """
    生成<manifest>根节点下的<remote>子节点，每个remote一个节点
    """
    for r in sorted(self.remotes):
      self._RemoteToXml(self.remotes[r], doc, root)
    if self.remotes:
      root.appendChild(doc.createTextNode(''))

    """
    生成<manifest>根节点下的<default>子节点

    更新manifest.xml根节点下的'default'子节点
    即: <default remote="..." revision="..." dest-branch="..." sync-j="1" sync-c="true" sync-s="true" />
    如: <default remote="github" revision="master"/>
    """
    have_default = False
    e = doc.createElement('default')
    if d.remote:
      have_default = True
      e.setAttribute('remote', d.remote.name)
    if d.revisionExpr:
      have_default = True
      e.setAttribute('revision', d.revisionExpr)
    if d.destBranchExpr:
      have_default = True
      e.setAttribute('dest-branch', d.destBranchExpr)
    if d.sync_j > 1:
      have_default = True
      e.setAttribute('sync-j', '%d' % d.sync_j)
    if d.sync_c:
      have_default = True
      e.setAttribute('sync-c', 'true')
    if d.sync_s:
      have_default = True
      e.setAttribute('sync-s', 'true')
    if have_default:
      root.appendChild(e)
      root.appendChild(doc.createTextNode(''))

    """
    生成<manifest>根节点下的<manifest-server>子节点
    即: <manifest-server url="..." />
    """
    if self._manifest_server:
      e = doc.createElement('manifest-server')
      e.setAttribute('url', self._manifest_server)
      root.appendChild(e)
      root.appendChild(doc.createTextNode(''))

    """
    遍历输出projects[]列表中的所有project，而且对于每一个project，还会输出其所有子project
    """
    def output_projects(parent, parent_node, projects):
      for project_name in projects:
        for project in self._projects[project_name]:
          output_project(parent, parent_node, project)

    def output_project(parent, parent_node, p):
      if not p.MatchesGroups(groups):
        return

      name = p.name
      relpath = p.relpath
      if parent:
        name = self._UnjoinName(parent.name, name)
        relpath = self._UnjoinRelpath(parent.relpath, relpath)

      """
      生成<project>节点
      即: <project name="..." path="..." remote="..." revision="..." upstream="..." dest-branch="..." groups="..." sync-c="..." sync-s="..." clone-depth="...">
            <copyfile src="..." dest="..." />
            <linkefile src="..." dest="..." />
            <annotation name="..." value="..." />
          </project>
      """
      e = doc.createElement('project')
      parent_node.appendChild(e)
      e.setAttribute('name', name)
      if relpath != name:
        e.setAttribute('path', relpath)
      remoteName = None
      if d.remote:
        remoteName = d.remote.name
      if not d.remote or p.remote.orig_name != remoteName:
        remoteName = p.remote.orig_name
        e.setAttribute('remote', remoteName)
      if peg_rev:
        if self.IsMirror:
          value = p.bare_git.rev_parse(p.revisionExpr + '^0')
        else:
          value = p.work_git.rev_parse(HEAD + '^0')
        e.setAttribute('revision', value)
        if peg_rev_upstream:
          if p.upstream:
            e.setAttribute('upstream', p.upstream)
          elif value != p.revisionExpr:
            # Only save the origin if the origin is not a sha1, and the default
            # isn't our value
            e.setAttribute('upstream', p.revisionExpr)
      else:
        revision = self.remotes[p.remote.orig_name].revision or d.revisionExpr
        if not revision or revision != p.revisionExpr:
          e.setAttribute('revision', p.revisionExpr)
        if p.upstream and p.upstream != p.revisionExpr:
          e.setAttribute('upstream', p.upstream)

      if p.dest_branch and p.dest_branch != d.destBranchExpr:
        e.setAttribute('dest-branch', p.dest_branch)

      for c in p.copyfiles:
        ce = doc.createElement('copyfile')
        ce.setAttribute('src', c.src)
        ce.setAttribute('dest', c.dest)
        e.appendChild(ce)

      for l in p.linkfiles:
        le = doc.createElement('linkfile')
        le.setAttribute('src', l.src)
        le.setAttribute('dest', l.dest)
        e.appendChild(le)

      default_groups = ['all', 'name:%s' % p.name, 'path:%s' % p.relpath]
      egroups = [g for g in p.groups if g not in default_groups]
      if egroups:
        e.setAttribute('groups', ','.join(egroups))

      for a in p.annotations:
        if a.keep == "true":
          ae = doc.createElement('annotation')
          ae.setAttribute('name', a.name)
          ae.setAttribute('value', a.value)
          e.appendChild(ae)

      if p.sync_c:
        e.setAttribute('sync-c', 'true')

      if p.sync_s:
        e.setAttribute('sync-s', 'true')

      if p.clone_depth:
        e.setAttribute('clone-depth', str(p.clone_depth))

      self._output_manifest_project_extras(p, e)

      """
      如果有子projects，递归输出其子projects
      """
      if p.subprojects:
        """
        先生成所有子projects的名字集合(set), 然后遍历输出集合中的所有projects
        """
        subprojects = set(subp.name for subp in p.subprojects)
        output_projects(p, e, list(sorted(subprojects)))

    """
    生成<manifest>根节点下的<project>子节点

    先生成projects的名字集合(set), 然后遍历输出集合中所有projects及其子projects
    """
    projects = set(p.name for p in self._paths.values() if not p.parent)
    output_projects(None, root, list(sorted(projects)))

    """
    生成<manifest>根节点下的<repo-hooks>子节点
    即: <repo-hooks in-project="..." enabled-list="..." />
    """
    if self._repo_hooks_project:
      root.appendChild(doc.createTextNode(''))
      e = doc.createElement('repo-hooks')
      e.setAttribute('in-project', self._repo_hooks_project.name)
      e.setAttribute('enabled-list',
                     ' '.join(self._repo_hooks_project.enabled_repo_hooks))
      root.appendChild(e)

    """
    将manifest内容使用'UTF-8'编码写入fd指定的文件中
    """
    doc.writexml(fd, '', '  ', '\n', 'UTF-8')

  def _output_manifest_project_extras(self, p, e):
    """Manifests can modify e if they support extra project attributes."""
    pass

  """
  返回manifest的path列表
  """
  @property
  def paths(self):
    self._Load()
    return self._paths

  """
  返回manifests的project列表
  """
  @property
  def projects(self):
    self._Load()
    return list(self._paths.values())

  """
  返回manifest的remote列表
  """
  @property
  def remotes(self):
    self._Load()
    return self._remotes

  """
  返回manifest的default节点对象
  """
  @property
  def default(self):
    self._Load()
    return self._default

  """
  返回manifest的repo-hooks节点对象
  """
  @property
  def repo_hooks_project(self):
    self._Load()
    return self._repo_hooks_project

  """
  返回manifest的notice节点对象
  """
  @property
  def notice(self):
    self._Load()
    return self._notice

  """
  返回manifest的manifest-server节点对象
  """
  @property
  def manifest_server(self):
    self._Load()
    return self._manifest_server

  """
  返回manifest库.git/config下的'repo.mirror'设置
  """
  @property
  def IsMirror(self):
    return self.manifestProject.config.GetBoolean('repo.mirror')

  """
  返回manifest库.git/config下的'repo.archive'设置
  """
  @property
  def IsArchive(self):
    return self.manifestProject.config.GetBoolean('repo.archive')

  """
  _Load()的反操作，清空项目的manifest信息。
  """
  def _Unload(self):
    self._loaded = False
    self._projects = {}
    self._paths = {}
    self._remotes = {}
    self._default = None
    self._repo_hooks_project = None
    self._notice = None
    self.branch = None
    self._manifest_server = None

  """
  在本地的清单库中，当前分支名默认为'default', _Load操作找到当前分支对应的原始分支用于设置branch成员。

  .repo/manifests$ cat .git/config
  ...
  [remote "origin"]
    url = https://android.googlesource.com/platform/manifest
    fetch = +refs/heads/*:refs/remotes/origin/*
  [branch "default"]
    remote = origin
    merge = refs/heads/android-4.0.1_r1
  处理后 self.branch = 'android-4.0.1_r1'

  同时，加载
  - 远程 manifest.xml以及
  - 本地 local_manifests目录下的所有xml文件(原来的方式是local_manifest.xml文件)
  中的所有nodes节点并进行解析。
  """
  def _Load(self):
    if not self._loaded:
      """
      $ git branch
      * default
      $ cat .git/config
      ...
      [remote "origin"]
        url = https://android.googlesource.com/platform/manifest
        fetch = +refs/heads/*:refs/remotes/origin/*
      [branch "default"]
              remote = origin
              merge = refs/heads/android-4.0.1_r1

      从以上操作可见，当前位于'default'分支，对于'default'分支，其：
      remote = origin
       merge = refs/heads/android-4.0.1_r1

      因此这里:
      m.CurrentBranch = 'default'
      m.GetBranch(m.CurrentBranch).merge = 'refs/heads/android-4.0.1_r1'

      经过处理后，b = 'android-4.0.1_r1'
      所以最终 self.branch = 'android-4.0.1_r1'
      """
      m = self.manifestProject
      b = m.GetBranch(m.CurrentBranch).merge
      if b is not None and b.startswith(R_HEADS):
        b = b[len(R_HEADS):]
      self.branch = b

      """
      #
      # 解析manifest文件的节点，并保存到nodes[]列表中，解析的文件包括3种:
      # 1. '.repo/manifest.xml'
      # 2. '.repo/local_manifest.xml'    (旧方式, 即原来的local manifest方式，建议使用新方式)
      # 3. '.repo/local_manifests/*.xml' (新方式, 建议采用的新的local manifest方式)
      #
      """

      """
      加载manifestFile ='/path/to/test/.repo/manifest.xml'中的nodes节点。

      实际上在Override(name)操作时，manifestFile可能指向具体名为name的manifest文件，如'manifests/rpi3.xml',
      加载完节点后，重新将manifestFile指回原来默认的xml文件，即'.repo/manifest.xml'
      """
      nodes = []
      nodes.append(self._ParseManifestXml(self.manifestFile,
                                          self.manifestProject.worktree))

      """
      local='/path/to/test/.repo/local_manifest.xml'

      如果local文件存在，提示一个警告信息，然后加载local指定的manifest文件的nodes节点。
      现在已经不提倡使用'/path/to/test/.repo/local_manifest.xml'文件来存放本地的manifest。

      新的方式建议将local的manifest存放到'/path/to/test/.repo/local_manifests'目录下。
      """
      local = os.path.join(self.repodir, LOCAL_MANIFEST_NAME)
      if os.path.exists(local):
        if not self.localManifestWarning:
          self.localManifestWarning = True
          print('warning: %s is deprecated; put local manifests in `%s` instead'
                % (LOCAL_MANIFEST_NAME, os.path.join(self.repodir, LOCAL_MANIFESTS_DIR_NAME)),
                file=sys.stderr)
        nodes.append(self._ParseManifestXml(local, self.repodir))

      """
      依次加载local_manifests目录'/path/to/test/.repo/local_manifests'目录下的所有xml文件的nodes节点。
      """
      local_dir = os.path.abspath(os.path.join(self.repodir, LOCAL_MANIFESTS_DIR_NAME))
      try:
        for local_file in sorted(os.listdir(local_dir)):
          if local_file.endswith('.xml'):
            local = os.path.join(local_dir, local_file)
            nodes.append(self._ParseManifestXml(local, self.repodir))
      except OSError:
        pass

      """
      解析上一步从manifest文件中提取的nodes[]节点

      _ParseManifest(nodes)操作会对nodes中的各类节点进行解析并存放到对应的类对象中，包括：
      _remotes, _default, _notice, _manifest_server, _paths, _projects[], _repo_hooks_project。
      """
      try:
        self._ParseManifest(nodes)
      except ManifestParseError as e:
        # There was a problem parsing, unload ourselves in case they catch
        # this error and try again later, we will show the correct error
        self._Unload()
        raise e

      """
      如果当前repo克隆时指定了'--mirror'选项，这里就将repoProject和manifestProject也添加到Mirror中。
      """
      if self.IsMirror:
        self._AddMetaProjectMirror(self.repoProject)
        self._AddMetaProjectMirror(self.manifestProject)

      self._loaded = True

  """
  加载include_root下path指定的xml文件，并将manifest节点下的所有子节点添加到nodes[]列表中。
  如果xml文件的manifest节包含'incude'节点，则递归加载incude指定的xml文件。

  实际上就是将manifest下的所有子节点添加到nodes[]列表中，对于'include'子节点，则递归加载'include'对应的文件。

  如：
  _ParseManifestXml(path='/path/to/test/.repo/manifest.xml', include_root='/path/to/test/.repo/manifests')
  """
  def _ParseManifestXml(self, path, include_root):
    try:
      root = xml.dom.minidom.parse(path)
    except (OSError, xml.parsers.expat.ExpatError) as e:
      raise ManifestParseError("error parsing manifest %s: %s" % (path, e))

    if not root or not root.childNodes:
      raise ManifestParseError("no root node in %s" % (path,))

    """
    找到名为'manifest'的节点
    """
    for manifest in root.childNodes:
      if manifest.nodeName == 'manifest':
        break
    else:
      raise ManifestParseError("no <manifest> in %s" % (path,))

    """
    如果节点中包含名为'include'的节点，则进一步递归解析'include'指示的xml文件。

    将所有的节点添加到nodes[]列表中。
    """
    nodes = []
    for node in manifest.childNodes:  # pylint:disable=W0631
                                      # We only get here if manifest is initialised
      """
      如果是'include'子节点，则调动_ParseManifestXml()递归解析
      """
      if node.nodeName == 'include':
        name = self._reqatt(node, 'name')
        fp = os.path.join(include_root, name)
        if not os.path.isfile(fp):
          raise ManifestParseError("include %s doesn't exist or isn't a file"
              % (name,))
        try:
          """
          递归加载'include'包含的xml文件
          """
          nodes.extend(self._ParseManifestXml(fp, include_root))
        # should isolate this to the exact exception, but that's
        # tricky.  actual parsing implementation may vary.
        except (KeyboardInterrupt, RuntimeError, SystemExit):
          raise
        except Exception as e:
          raise ManifestParseError(
              "failed parsing included manifest %s: %s", (name, e))
      else:
        """
        对于除'include'外的其它节点，则直接将解析得到的节点添加到nodes列表中
        """
        nodes.append(node)
    return nodes


  """
  对_ParseManifestXml()解析得到的节点列表nodes[]进一步分类处理，生成以下对象:
  _remotes, _default, _notice, _manifest_server, _paths, _projects[], _repo_hooks_project。

  调用顺序如下:
  1. 调用_ParseManifestXml()解析xml文件的子节点并存放到nodes[]列表中

    nodes = []
    nodes.append(_ParseManifestXml(manifestFile, manifestProject.worktree))

  2. 调用_ParseManifest(nodes)解析每一个节点nodes的属性

    _ParseManifest(nodes)
  """
  def _ParseManifest(self, node_list):
    """
    循环解析节点列表中的节点

    对于'remote'节点，解析并构造_Remote对象，然后添加到_remotes字典中。
    如:
    aosp: <remote  name="aosp"  fetch=".." />
    """
    for node in itertools.chain(*node_list):
      if node.nodeName == 'remote':
        remote = self._ParseRemote(node)
        if remote:
          if remote.name in self._remotes:
            if remote != self._remotes[remote.name]:
              raise ManifestParseError(
                  'remote %s already exists with different attributes' %
                  (remote.name))
          else:
            self._remotes[remote.name] = remote

    """
    对于'default'节点，解析并构造_Default对象，用于设置_default成员
    如:
    aosp: <default revision="refs/tags/android-4.0.1_r1" remote="aosp" sync-j="4" />
    """
    for node in itertools.chain(*node_list):
      if node.nodeName == 'default':
        new_default = self._ParseDefault(node)
        if self._default is None:
          self._default = new_default
        elif new_default != self._default:
          raise ManifestParseError('duplicate default in %s' %
                                   (self.manifestFile))

    if self._default is None:
      self._default = _Default()

    """
    对于'notice'节点，解析并构造_Notice对象，用于设置_notice成员

    很多manifest节点不包含'notice'节点
    """
    for node in itertools.chain(*node_list):
      if node.nodeName == 'notice':
        if self._notice is not None:
          raise ManifestParseError(
              'duplicate notice in %s' %
              (self.manifestFile))
        self._notice = self._ParseNotice(node)

    """
    对于'manifest-server'节点，解析并用于设置_manifest_server成员

    如：
    <manifest-server url="http://android-smartsync.corp.google.com/manifestserver"/>
    """
    for node in itertools.chain(*node_list):
      if node.nodeName == 'manifest-server':
        url = self._reqatt(node, 'url')
        if self._manifest_server is not None:
          raise ManifestParseError(
              'duplicate manifest-server in %s' %
              (self.manifestFile))
        self._manifest_server = url

    """
    递归添加project及其所有子projects
    """
    def recursively_add_projects(project):
      projects = self._projects.setdefault(project.name, [])
      if project.relpath is None:
        raise ManifestParseError(
            'missing path for %s in %s' %
            (project.name, self.manifestFile))
      if project.relpath in self._paths:
        raise ManifestParseError(
            'duplicate path %s in %s' %
            (project.relpath, self.manifestFile))
      self._paths[project.relpath] = project
      projects.append(project)
      for subproject in project.subprojects:
        recursively_add_projects(subproject)

    """
    解析nodes[]列表中的其它节点，包括：
    - 'project'
    - 'extend-project'
    - 'repo-hooks'
    - 'remove-project'
    """
    for node in itertools.chain(*node_list):
      """
      对于'project'节点，调用_ParseProject(node)解析并构造_Project对象，然后递归添加project节点的所有子projects节点
      如：
      op-tee: <project path="build" name="OP-TEE/build.git" revision="refs/tags/3.2.0" clone-depth="1">
        aosp: <project path="abi/cpp" name="platform/abi/cpp" />
      """
      if node.nodeName == 'project':
        project = self._ParseProject(node)
        recursively_add_projects(project)
      if node.nodeName == 'extend-project':
        name = self._reqatt(node, 'name')

        if name not in self._projects:
          raise ManifestParseError('extend-project element specifies non-existent '
                                   'project: %s' % name)

        path = node.getAttribute('path')
        groups = node.getAttribute('groups')
        if groups:
          groups = self._ParseGroups(groups)

        for p in self._projects[name]:
          if path and p.relpath != path:
            continue
          if groups:
            p.groups.extend(groups)
      if node.nodeName == 'repo-hooks':
        # Get the name of the project and the (space-separated) list of enabled.
        repo_hooks_project = self._reqatt(node, 'in-project')
        enabled_repo_hooks = self._reqatt(node, 'enabled-list').split()

        # Only one project can be the hooks project
        if self._repo_hooks_project is not None:
          raise ManifestParseError(
              'duplicate repo-hooks in %s' %
              (self.manifestFile))

        # Store a reference to the Project.
        try:
          repo_hooks_projects = self._projects[repo_hooks_project]
        except KeyError:
          raise ManifestParseError(
              'project %s not found for repo-hooks' %
              (repo_hooks_project))

        if len(repo_hooks_projects) != 1:
          raise ManifestParseError(
              'internal error parsing repo-hooks in %s' %
              (self.manifestFile))
        self._repo_hooks_project = repo_hooks_projects[0]

        # Store the enabled hooks in the Project object.
        self._repo_hooks_project.enabled_repo_hooks = enabled_repo_hooks
      if node.nodeName == 'remove-project':
        name = self._reqatt(node, 'name')

        if name not in self._projects:
          raise ManifestParseError('remove-project element specifies non-existent '
                                   'project: %s' % name)

        for p in self._projects[name]:
          del self._paths[p.relpath]
        del self._projects[name]

        # If the manifest removes the hooks project, treat it as if it deleted
        # the repo-hooks element too.
        if self._repo_hooks_project and (self._repo_hooks_project.name == name):
          self._repo_hooks_project = None


  def _AddMetaProjectMirror(self, m):
    name = None
    m_url = m.GetRemote(m.remote.name).url
    if m_url.endswith('/.git'):
      raise ManifestParseError('refusing to mirror %s' % m_url)

    if self._default and self._default.remote:
      url = self._default.remote.resolvedFetchUrl
      if not url.endswith('/'):
        url += '/'
      if m_url.startswith(url):
        remote = self._default.remote
        name = m_url[len(url):]

    if name is None:
      s = m_url.rindex('/') + 1
      manifestUrl = self.manifestProject.config.GetString('remote.origin.url')
      remote = _XmlRemote('origin', fetch=m_url[:s], manifestUrl=manifestUrl)
      name = m_url[s:]

    if name.endswith('.git'):
      name = name[:-4]

    if name not in self._projects:
      m.PreSync()
      gitdir = os.path.join(self.topdir, '%s.git' % name)
      project = Project(manifest = self,
                        name = name,
                        remote = remote.ToRemoteSpec(name),
                        gitdir = gitdir,
                        objdir = gitdir,
                        worktree = None,
                        relpath = name or None,
                        revisionExpr = m.revisionExpr,
                        revisionId = None)
      self._projects[project.name] = [project]
      self._paths[project.relpath] = project

  """
  解析manifest中'remote'节点的属性，并用于构建_XmlRemote()对象。

  如：
  op-tee: <remote name="github" fetch="https://github.com" />
    aosp: <remote  name="aosp"  fetch=".." />
  others: <remote  name="bcg"   fetch="ssh://gitbsesw@xxx.xxx.xxx/aosp"  review="http://xxx.xxx.xxx:8081/" />
  """
  def _ParseRemote(self, node):
    """
    reads a <remote> element from the manifest file
    """
    """
    解析<remote>节点的'name', 'alias', 'fetch', 'pushurl', 'review'和'revision'属性
    """
    name = self._reqatt(node, 'name')
    alias = node.getAttribute('alias')
    if alias == '':
      alias = None
    fetch = self._reqatt(node, 'fetch')
    pushUrl = node.getAttribute('pushurl')
    if pushUrl == '':
      pushUrl = None
    review = node.getAttribute('review')
    if review == '':
      review = None
    revision = node.getAttribute('revision')
    if revision == '':
      revision = None
    """
    获取.repo/manifests/.git/config中remote.origin.url的属性，并赋值给manifestUrl。
    如：
    .repo/manifests$ cat .git/config
    ...
    [remote "origin"]
            url = https://android.googlesource.com/platform/manifest
            fetch = +refs/heads/*:refs/remotes/origin/*
    ...
    """
    manifestUrl = self.manifestProject.config.GetString('remote.origin.url')
    return _XmlRemote(name, alias, fetch, pushUrl, manifestUrl, review, revision)

  """
  解析manifest中'default'节点的属性，并用于构建_Default对象。

  如：
  op-tee: <default remote="github" revision="master" />
    aosp: <default revision="refs/tags/android-4.0.1_r1" remote="aosp" sync-j="4" />
  others: <default remote="yvr" revision="p-tv-dev" sync-j="4" />
  """
  def _ParseDefault(self, node):
    """
    reads a <default> element from the manifest file
    """
    d = _Default()
    d.remote = self._get_remote(node)
    d.revisionExpr = node.getAttribute('revision')
    if d.revisionExpr == '':
      d.revisionExpr = None

    d.destBranchExpr = node.getAttribute('dest-branch') or None

    sync_j = node.getAttribute('sync-j')
    if sync_j == '' or sync_j is None:
      d.sync_j = 1
    else:
      d.sync_j = int(sync_j)

    sync_c = node.getAttribute('sync-c')
    if not sync_c:
      d.sync_c = False
    else:
      d.sync_c = sync_c.lower() in ("yes", "true", "1")

    sync_s = node.getAttribute('sync-s')
    if not sync_s:
      d.sync_s = False
    else:
      d.sync_s = sync_s.lower() in ("yes", "true", "1")
    return d

  """
  解析manifest中的'notice'节点
  """
  def _ParseNotice(self, node):
    """
    reads a <notice> element from the manifest file

    The <notice> element is distinct from other tags in the XML in that the
    data is conveyed between the start and end tag (it's not an empty-element
    tag).

    The white space (carriage returns, indentation) for the notice element is
    relevant and is parsed in a way that is based on how python docstrings work.
    In fact, the code is remarkably similar to here:
      http://www.python.org/dev/peps/pep-0257/
    """
    # Get the data out of the node...
    notice = node.childNodes[0].data

    # Figure out minimum indentation, skipping the first line (the same line
    # as the <notice> tag)...
    minIndent = sys.maxsize
    lines = notice.splitlines()
    for line in lines[1:]:
      lstrippedLine = line.lstrip()
      if lstrippedLine:
        indent = len(line) - len(lstrippedLine)
        minIndent = min(indent, minIndent)

    # Strip leading / trailing blank lines and also indentation.
    cleanLines = [lines[0].strip()]
    for line in lines[1:]:
      cleanLines.append(line[minIndent:].rstrip())

    # Clear completely blank lines from front and back...
    while cleanLines and not cleanLines[0]:
      del cleanLines[0]
    while cleanLines and not cleanLines[-1]:
      del cleanLines[-1]

    return '\n'.join(cleanLines)

  """
  将parent_name和name连接在一起，返回'parent_name/name'
  如: _JoinName("build", "google") --> 'build/google'
  """
  def _JoinName(self, parent_name, name):
    return os.path.join(parent_name, name)

  """
  拆分name相对于parent_name的路径
  如: _UnjoinName('build', 'build/google') --> google
  """
  def _UnjoinName(self, parent_name, name):
    return os.path.relpath(name, parent_name)

  """
  解析manifest中的'project'节点。
  使用节点的'name', 'revision', 'path', 'rebase', 'sync-c', 'sync-s', 'clone-depth', 'dest-branch', 'upstream', 'groups'构建_Prject对象。

  如：
  op-tee: <project path="build" name="OP-TEE/build.git" revision="refs/tags/3.2.0" clone-depth="1">
    aosp: <project path="abi/cpp" name="platform/abi/cpp" />
  """
  def _ParseProject(self, node, parent = None, **extra_proj_attrs):
    """
    reads a <project> element from the manifest file
    """
    """
    project的name属性
    """
    name = self._reqatt(node, 'name')
    if parent:
      name = self._JoinName(parent.name, name)

    """
    project的remote属性
    """
    remote = self._get_remote(node)
    if remote is None:
      remote = self._default.remote
    if remote is None:
      raise ManifestParseError("no remote for project %s within %s" %
            (name, self.manifestFile))

    """
    project的revision属性
    """
    revisionExpr = node.getAttribute('revision') or remote.revision
    if not revisionExpr:
      revisionExpr = self._default.revisionExpr
    if not revisionExpr:
      raise ManifestParseError("no revision for project %s within %s" %
            (name, self.manifestFile))

    """
    project的path属性
    """
    path = node.getAttribute('path')
    if not path:
      path = name
    if path.startswith('/'):
      raise ManifestParseError("project %s path cannot be absolute in %s" %
            (name, self.manifestFile))

    """
    project的rebase属性
    """
    rebase = node.getAttribute('rebase')
    if not rebase:
      rebase = True
    else:
      rebase = rebase.lower() in ("yes", "true", "1")

    """
    project的sync-c属性
    """
    sync_c = node.getAttribute('sync-c')
    if not sync_c:
      sync_c = False
    else:
      sync_c = sync_c.lower() in ("yes", "true", "1")

    """
    project的sync-s属性
    """
    sync_s = node.getAttribute('sync-s')
    if not sync_s:
      sync_s = self._default.sync_s
    else:
      sync_s = sync_s.lower() in ("yes", "true", "1")

    """
    project的clone-depth属性
    """
    clone_depth = node.getAttribute('clone-depth')
    if clone_depth:
      try:
        clone_depth = int(clone_depth)
        if  clone_depth <= 0:
          raise ValueError()
      except ValueError:
        raise ManifestParseError('invalid clone-depth %s in %s' %
                                 (clone_depth, self.manifestFile))

    """
    project的dest-branch属性
    """
    dest_branch = node.getAttribute('dest-branch') or self._default.destBranchExpr

    """
    project的upstream属性
    """
    upstream = node.getAttribute('upstream')

    """
    project的upstream属性

    节点含有'groups'属性的情况：
    如：<project groups="pdk" name="platform/bootable/recovery" path="bootable/recovery" />
    """
    groups = ''
    if node.hasAttribute('groups'):
      groups = node.getAttribute('groups')
    groups = self._ParseGroups(groups)

    """
    根据partent设置，得到git库的相关路径
    """
    if parent is None:
      relpath, worktree, gitdir, objdir = self.GetProjectPaths(name, path)
    else:
      relpath, worktree, gitdir, objdir = \
          self.GetSubprojectPaths(parent, name, path)

    """
    默认的groups属性为'all'
    """
    default_groups = ['all', 'name:%s' % name, 'path:%s' % relpath]
    groups.extend(set(default_groups).difference(groups))

    if self.IsMirror and node.hasAttribute('force-path'):
      if node.getAttribute('force-path').lower() in ("yes", "true", "1"):
        gitdir = os.path.join(self.topdir, '%s.git' % path)

    """
    针对每一个project构建一个Project对象
    """
    project = Project(manifest = self,
                      name = name,
                      remote = remote.ToRemoteSpec(name),
                      gitdir = gitdir,
                      objdir = objdir,
                      worktree = worktree,
                      relpath = relpath,
                      revisionExpr = revisionExpr,
                      revisionId = None,
                      rebase = rebase,
                      groups = groups,
                      sync_c = sync_c,
                      sync_s = sync_s,
                      clone_depth = clone_depth,
                      upstream = upstream,
                      parent = parent,
                      dest_branch = dest_branch,
                      **extra_proj_attrs)

    """
    检查project节点的子节点，包括:
    - copyfile
    - linkfile
    - annotation
    - project
    """
    for n in node.childNodes:
      if n.nodeName == 'copyfile':
        self._ParseCopyFile(project, n)
      if n.nodeName == 'linkfile':
        self._ParseLinkFile(project, n)
      if n.nodeName == 'annotation':
        self._ParseAnnotation(project, n)
      if n.nodeName == 'project':
        project.subprojects.append(self._ParseProject(n, parent = project))

    return project

  """
  返回名为name的project对应的git相关路径(包括worktree, gitdir, objdir)

  假设: topdir='/path/to/test', path='build' name='google'

  1. mirror仓库，返回路径:
     worktree = None
       gitdir = '/path/to/test/google.git'
       objdir = '/path/to/test/google.git'

  2. 普通仓库，返回路径:
     worktree = '/path/to/test/build'
       gitdir = '/path/to/test/.repo/projects/build.git'
       objdir = '/path/to/test/.repo/project-objects/google.git'
  """
  def GetProjectPaths(self, name, path):
    relpath = path
    if self.IsMirror:
      worktree = None
      gitdir = os.path.join(self.topdir, '%s.git' % name)
      objdir = gitdir
    else:
      worktree = os.path.join(self.topdir, path).replace('\\', '/')
      gitdir = os.path.join(self.repodir, 'projects', '%s.git' % path)
      objdir = os.path.join(self.repodir, 'project-objects', '%s.git' % name)
    return relpath, worktree, gitdir, objdir

  def GetProjectsWithName(self, name):
    return self._projects.get(name, [])

  def GetSubprojectName(self, parent, submodule_path):
    return os.path.join(parent.name, submodule_path)

  """
  将parent_relpath和relpath连接在一起，返回'parent_relpath/relpath'
  如: _JoinRelpath("build", "google") --> 'build/google'
  """
  def _JoinRelpath(self, parent_relpath, relpath):
    return os.path.join(parent_relpath, relpath)

  """
  拆分relpath相对于parent_relpath的路径
  如: _UnjoinRelpath('build', 'build/google') --> google
  """
  def _UnjoinRelpath(self, parent_relpath, relpath):
    return os.path.relpath(relpath, parent_relpath)

  def GetSubprojectPaths(self, parent, name, path):
    relpath = self._JoinRelpath(parent.relpath, path)
    gitdir = os.path.join(parent.gitdir, 'subprojects', '%s.git' % path)
    objdir = os.path.join(parent.gitdir, 'subproject-objects', '%s.git' % name)
    if self.IsMirror:
      worktree = None
    else:
      worktree = os.path.join(parent.worktree, path).replace('\\', '/')
    return relpath, worktree, gitdir, objdir

  """
  解析copyfile节点，并添加到对应的project中
  即: <copyfile src="..." dest="..." />
  """
  def _ParseCopyFile(self, project, node):
    src = self._reqatt(node, 'src')
    dest = self._reqatt(node, 'dest')
    if not self.IsMirror:
      # src is project relative;
      # dest is relative to the top of the tree
      project.AddCopyFile(src, dest, os.path.join(self.topdir, dest))

  """
  解析linkfile节点，并添加到对应的project中
  即: <linkfile src="..." dest="..." />
  """
  def _ParseLinkFile(self, project, node):
    src = self._reqatt(node, 'src')
    dest = self._reqatt(node, 'dest')
    if not self.IsMirror:
      # src is project relative;
      # dest is relative to the top of the tree
      project.AddLinkFile(src, dest, os.path.join(self.topdir, dest))

  """
  解析annotation节点，并添加到对应的project中
  即: <annotation name="..." value="..." keep="..." />
  """
  def _ParseAnnotation(self, project, node):
    name = self._reqatt(node, 'name')
    value = self._reqatt(node, 'value')
    try:
      keep = self._reqatt(node, 'keep').lower()
    except ManifestParseError:
      keep = "true"
    if keep != "true" and keep != "false":
      raise ManifestParseError('optional "keep" attribute must be '
            '"true" or "false"')
    project.AddAnnotation(name, value, keep)

  """
  提取node节点的'remote'属性，并返回manifest中此remote相应的对象。
  """
  def _get_remote(self, node):
    """
    提取node节点的'remote'属性，保存的实际上是一个remote对象的name
    """
    name = node.getAttribute('remote')
    if not name:
      return None

    """
    从manifest的_remotes[]列表中返回名为name的_XmlRemote()对象。
    """
    v = self._remotes.get(name)
    if not v:
      raise ManifestParseError("remote %s not defined in %s" %
            (name, self.manifestFile))
    return v

  """
  提取node节点名为attname的属性
  """
  def _reqatt(self, node, attname):
    """
    reads a required attribute from the node.
    """
    v = node.getAttribute(attname)
    if not v:
      raise ManifestParseError("no %s in <%s> within %s" %
            (attname, node.nodeName, self.manifestFile))
    return v

  """
  比较本地manifest和指定manifest的project差异, 包括added/removed/changed
  """
  def projectsDiff(self, manifest):
    """return the projects differences between two manifests.

    The diff will be from self to given manifest.

    """
    fromProjects = self.paths
    toProjects = manifest.paths

    fromKeys = sorted(fromProjects.keys())
    toKeys = sorted(toProjects.keys())

    diff = {'added': [], 'removed': [], 'changed': [], 'unreachable': []}

    """
    遍历本地manifest中的paths，逐个比较在两个manifest中的状态
    """
    for proj in fromKeys:
      """
      不在对比manifest的paths中，说明已经移除了(removed)
      """
      if not proj in toKeys:
        diff['removed'].append(fromProjects[proj])
      else:
        fromProj = fromProjects[proj]
        toProj = toProjects[proj]
        """
        比较两个paths的revision，如果二者不等，说明已经改变了(changed)
        """
        try:
          fromRevId = fromProj.GetCommitRevisionId()
          toRevId = toProj.GetCommitRevisionId()
        except ManifestInvalidRevisionError:
          diff['unreachable'].append((fromProj, toProj))
        else:
          if fromRevId != toRevId:
            diff['changed'].append((fromProj, toProj))
        """
        每检查完一个，就从对比的manifest中删除相应的项
        """
        toKeys.remove(proj)

    """
    删除完本地manifest中对应的项，对比manifest中剩余的项就是新增的了
    """
    for proj in toKeys:
      diff['added'].append(toProjects[proj])

    return diff


class GitcManifest(XmlManifest):

  def __init__(self, repodir, gitc_client_name):
    """Initialize the GitcManifest object."""
    super(GitcManifest, self).__init__(repodir)
    self.isGitcClient = True
    self.gitc_client_name = gitc_client_name
    self.gitc_client_dir = os.path.join(gitc_utils.get_gitc_manifest_dir(),
                                        gitc_client_name)
    self.manifestFile = os.path.join(self.gitc_client_dir, '.manifest')

  def _ParseProject(self, node, parent = None):
    """Override _ParseProject and add support for GITC specific attributes."""
    return super(GitcManifest, self)._ParseProject(
        node, parent=parent, old_revision=node.getAttribute('old-revision'))

  def _output_manifest_project_extras(self, p, e):
    """Output GITC Specific Project attributes"""
    if p.old_revision:
      e.setAttribute('old-revision', str(p.old_revision))

