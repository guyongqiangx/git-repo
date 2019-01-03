# -*- coding: utf-8 -*-
#
# Copyright (C) 2011 The Android Open Source Project
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
import sys

from command import Command, MirrorSafeCommand

"""
$ repo help list

Summary
-------
List projects and their associated directories

Usage: repo list [-f] [<project>...]
repo list [-f] -r str1 [str2]..."

Options:
  -h, --help            show this help message and exit
  -r, --regex           Filter the project list based on regex or wildcard
                        matching of strings
  -g GROUPS, --groups=GROUPS
                        Filter the project list based on the groups the
                        project is in
  -f, --fullpath        Display the full work tree path instead of the
                        relative path
  -n, --name-only       Display only the name of the repository
  -p, --path-only       Display only the path of the repository

Description
-----------
List all projects; pass '.' to list the project for the cwd.

This is similar to running: repo forall -c 'echo "$REPO_PATH :
$REPO_PROJECT"'.
"""
class List(Command, MirrorSafeCommand):
  common = True
  helpSummary = "List projects and their associated directories"
  helpUsage = """
%prog [-f] [<project>...]
%prog [-f] -r str1 [str2]..."
"""
  helpDescription = """
List all projects; pass '.' to list the project for the cwd.

This is similar to running: repo forall -c 'echo "$REPO_PATH : $REPO_PROJECT"'.
"""

  """
  定义'repo list'命令的参数选项
  """
  def _Options(self, p):
    p.add_option('-r', '--regex',
                 dest='regex', action='store_true',
                 help="Filter the project list based on regex or wildcard matching of strings")
    p.add_option('-g', '--groups',
                 dest='groups',
                 help="Filter the project list based on the groups the project is in")
    p.add_option('-f', '--fullpath',
                 dest='fullpath', action='store_true',
                 help="Display the full work tree path instead of the relative path")
    p.add_option('-n', '--name-only',
                 dest='name_only', action='store_true',
                 help="Display only the name of the repository")
    p.add_option('-p', '--path-only',
                 dest='path_only', action='store_true',
                 help="Display only the path of the repository")

  """
  'repo list'命令中'list'操作的主函数。

  返回符合查找条件的project列表，如：
  1. 列举名字或路径中包含'sdk'字符串的project
  $ repo list -r sdk
  external/dng_sdk : platform/external/dng_sdk
  prebuilts/sdk : platform/prebuilts/sdk
  sdk : platform/sdk

  2. 列举名字中包含'sdk'字符串的project
  $ repo list -r sdk -n
  platform/external/dng_sdk
  platform/prebuilts/sdk
  platform/sdk

  3. 列举路径中包含'sdk'字符串的project
  $ repo list -r sdk -p
  external/dng_sdk
  prebuilts/sdk
  sdk
  """
  def Execute(self, opt, args):
    """List all projects and the associated directories.

    This may be possible to do with 'repo forall', but repo newbies have
    trouble figuring that out.  The idea here is that it should be more
    discoverable.

    Args:
      opt: The options.
      args: Positional args.  Can be a list of projects to list, or empty.
    """

    if opt.fullpath and opt.name_only:
      print('error: cannot combine -f and -n', file=sys.stderr)
      sys.exit(1)

    """
    如果不带正则选项'-r'，则根据groups参数调用GetProjects()获取相应的project节点列表；
    如果带有正则选项，则根据正则表达式调用FindProjects()获取相应的project节点列表；
    """
    if not opt.regex:
      projects = self.GetProjects(args, groups=opt.groups)
    else:
      projects = self.FindProjects(args)

    def _getpath(x):
      if opt.fullpath:
        return x.worktree
      return x.relpath

    """
    将按照条件查找得到的project按格式整理存放到lines列表中，用于格式化输出。
    """
    lines = []
    for project in projects:
      if opt.name_only and not opt.path_only:
        lines.append("%s" % ( project.name))
      elif opt.path_only and not opt.name_only:
        lines.append("%s" % (_getpath(project)))
      else:
        lines.append("%s : %s" % (_getpath(project), project.name))

    lines.sort()
    print('\n'.join(lines))
