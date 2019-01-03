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

import os
import optparse
import platform
import re
import sys

from error import NoSuchProjectError
from error import InvalidProjectGroupsError


"""
Command类为所有repo命令的基类，用于command操作的调用，其公开的接口包括：
成员变量:
  common
  manifest
成员函数:
  WantPager(_opt)
  ReadEnvironmentOptions(opts)
  OptionParser()
  Usage()
  Execute(opt, args)
  GetProjects(args, manifest=None, groups='', missing_ok=False, submodules_ok=False)
  FindProjects(args, inverse=False)
"""
class Command(object):
  """Base class for any command line action in repo.
  """

  common = False
  manifest = None
  _optparse = None

  def WantPager(self, _opt):
    return False

  """
  检查opts中依赖于环境变量的选项，并使用相应环境变量来设置该选项的值。

  例如，'init.py'中设定'init'命令的选项'reference'和'manifest_url'需要分别检查环境变量'REPO_MIRROR_LOCATION'和'REPO_MANIFEST_URL'
  所以这里需要使用这两个环境变量的值来更新'opts'中的'reference'和'manifest_url'。

  所有的子命令中，也只有'subcmds/init.py'定义了环境变量依赖。
  """
  def ReadEnvironmentOptions(self, opts):
    """ Set options from environment variables. """

    """
    repo init: 'repo init -u https://android.googlesource.com/platform/manifest -b android-4.0.1_r1'
    opts: {    'archive': None,
           'config_name': False,
                 'depth': None,
                'groups': 'default',
       'manifest_branch': 'android-4.0.1_r1',
         'manifest_name': 'default.xml',
          'manifest_url': 'https://android.googlesource.com/platform/manifest',
                'mirror': None,
       'no_clone_bundle': None,
        'no_repo_verify': None,
              'platform': 'auto',
                 'quiet': False,
             'reference': None,
           'repo_branch': None,
              'repo_rul': None}

    init.py中定义了函数_RegisteredEnvironmentOptions()，返回：
    env_options: {'REPO_MIRROR_LOCATION': 'reference',
                     'REPO_MANIFEST_URL': 'manifest_url'}
    因此，这里说明'reference'和'manifest_url'需要分别检查环境变量'REPO_MIRROR_LOCATION'和'REPO_MANIFEST_URL'
    """
    env_options = self._RegisteredEnvironmentOptions()

    for env_key, opt_key in env_options.items():
      # Get the user-set option value if any
      """
      init.py中定义的init命令需要检查的选项和相应的环境变量：
                     env_key | opt_key
                     ------- | -------
      'REPO_MIRROR_LOCATION' | 'reference'
         'REPO_MANIFEST_URL' | 'manifest_url'
      """
      opt_value = getattr(opts, opt_key)

      # If the value is set, it means the user has passed it as a command
      # line option, and we should use that.  Otherwise we can try to set it
      # with the value from the corresponding environment variable.
      if opt_value is not None:
        continue

      """
      使用环境变量中的值来更新opts选项中的对应值。
      """
      env_value = os.environ.get(env_key)
      if env_value is not None:
        setattr(opts, opt_key, env_value)

    return opts

  """
  构建Command的OptionParser属性

  外界的调用方式：
  cmd.OptionParser.parse_args(argv)
  """
  @property
  def OptionParser(self):
    if self._optparse is None:
      try:
        me = 'repo %s' % self.NAME
        usage = self.helpUsage.strip().replace('%prog', me)
      except AttributeError:
        usage = 'repo %s' % self.NAME
      self._optparse = optparse.OptionParser(usage=usage)
      self._Options(self._optparse)
    return self._optparse

  def _Options(self, p):
    """Initialize the option parser.
    """

  """
  设置命令依赖的环境变量，默认为空，需要由具体的子类来实现

  所有的子命令中，也只有'subcmds/init.py'定义了环境变量依赖。
  """
  def _RegisteredEnvironmentOptions(self):
    """Get options that can be set from environment variables.

    Return a dictionary mapping environment variable name
    to option key name that it can override.

    Example: {'REPO_MY_OPTION': 'my_option'}

    Will allow the option with key value 'my_option' to be set
    from the value in the environment variable named 'REPO_MY_OPTION'.

    Note: This does not work properly for options that are explicitly
    set to None by the user, or options that are defined with a
    default value other than None.

    """
    return {}

  def Usage(self):
    """Display usage and terminate.
    """
    self.OptionParser.print_usage()
    sys.exit(1)

  """
  命令的执行函数，由Command的具体命令子类来实现。
  """
  def Execute(self, opt, args):
    """Perform the action, after option parsing is complete.
    """
    raise NotImplementedError

  """
  使用projects中每个project的worktree来构建一个(worktree, project)的字典。

  这里的_by_path是一个包含(worktree, project)的字典。
  """
  def _ResetPathToProjectMap(self, projects):
    self._by_path = dict((p.worktree, p) for p in projects)

  """
  更新_py_path字典中'project.worktree'键对应的project值。
  """
  def _UpdatePathToProjectMap(self, project):
    self._by_path[project.worktree] = project

  def _GetProjectByPath(self, manifest, path):
    project = None
    if os.path.exists(path):
      oldpath = None
      while path and \
            path != oldpath and \
            path != manifest.topdir:
        try:
          project = self._by_path[path]
          break
        except KeyError:
          oldpath = path
          path = os.path.dirname(path)
      if not project and path == manifest.topdir:
        try:
          project = self._by_path[path]
        except KeyError:
          pass
    else:
      try:
        project = self._by_path[path]
      except KeyError:
        pass
    return project

  """
  根据传入的参数，返回manifest中所有满足条件的project节点。
  """
  def GetProjects(self, args, manifest=None, groups='', missing_ok=False,
                  submodules_ok=False):
    """A list of projects that match the arguments.
    """
    if not manifest:
      manifest = self.manifest
    all_projects_list = manifest.projects
    result = []

    mp = manifest.manifestProject

    """
    如果没有指定groups，则读取'.repo/manifests/.git/config'中的'manifest.groups'属性。

    如果配置文件中不存在groups属性，则默认groups为'default,platform-linux'属性(针对linux平台)
    """
    if not groups:
      groups = mp.config.GetString('manifest.groups')
    if not groups:
      groups = 'default,platform-' + platform.system().lower()
    groups = [x for x in re.split(r'[,\s]+', groups) if x]

    if not args:
      derived_projects = {}
      for project in all_projects_list:
        if submodules_ok or project.sync_s:
          derived_projects.update((p.name, p)
                                  for p in project.GetDerivedSubprojects())
      all_projects_list.extend(derived_projects.values())
      for project in all_projects_list:
        if (missing_ok or project.Exists) and project.MatchesGroups(groups):
          result.append(project)
    else:
      self._ResetPathToProjectMap(all_projects_list)

      for arg in args:
        projects = manifest.GetProjectsWithName(arg)

        if not projects:
          path = os.path.abspath(arg).replace('\\', '/')
          project = self._GetProjectByPath(manifest, path)

          # If it's not a derived project, update path->project mapping and
          # search again, as arg might actually point to a derived subproject.
          if (project and not project.Derived and (submodules_ok or
                                                   project.sync_s)):
            search_again = False
            for subproject in project.GetDerivedSubprojects():
              self._UpdatePathToProjectMap(subproject)
              search_again = True
            if search_again:
              project = self._GetProjectByPath(manifest, path) or project

          if project:
            projects = [project]

        if not projects:
          raise NoSuchProjectError(arg)

        for project in projects:
          if not missing_ok and not project.Exists:
            raise NoSuchProjectError(arg)
          if not project.MatchesGroups(groups):
            raise InvalidProjectGroupsError(arg)

        result.extend(projects)

    def _getpath(x):
      return x.relpath
    result.sort(key=_getpath)
    return result

  def FindProjects(self, args, inverse=False):
    result = []
    patterns = [re.compile(r'%s' % a, re.IGNORECASE) for a in args]
    for project in self.GetProjects(''):
      for pattern in patterns:
        match = pattern.search(project.name) or pattern.search(project.relpath)
        if not inverse and match:
          result.append(project)
          break
        if inverse and match:
          break
      else:
        if inverse:
          result.append(project)
    result.sort(key=lambda project: project.relpath)
    return result


# pylint: disable=W0223
# Pylint warns that the `InteractiveCommand` and `PagedCommand` classes do not
# override method `Execute` which is abstract in `Command`.  Since that method
# is always implemented in classes derived from `InteractiveCommand` and
# `PagedCommand`, this warning can be suppressed.
class InteractiveCommand(Command):
  """Command which requires user interaction on the tty and
     must not run within a pager, even if the user asks to.
  """
  def WantPager(self, _opt):
    return False


class PagedCommand(Command):
  """Command which defaults to output in a pager, as its
     display tends to be larger than one screen full.
  """
  def WantPager(self, _opt):
    return True

# pylint: enable=W0223


class MirrorSafeCommand(object):
  """Command permits itself to run within a mirror,
     and does not require a working directory.
  """


class GitcAvailableCommand(object):
  """Command that requires GITC to be available, but does
     not require the local client to be a GITC client.
  """


class GitcClientCommand(object):
  """Command that requires the local client to be a GITC
     client.
  """
