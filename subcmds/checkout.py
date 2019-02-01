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

from __future__ import print_function
import sys
from command import Command
from progress import Progress

"""
$ repo help checkout

Summary
-------
Checkout a branch for development

Usage: repo checkout <branchname> [<project>...]

Options:
  -h, --help  show this help message and exit

Description
-----------
The 'repo checkout' command checks out an existing branch that was
previously created by 'repo start'.

The command is equivalent to:

  repo forall [<project>...] -c git checkout <branchname>

"""
class Checkout(Command):
  common = True
  helpSummary = "Checkout a branch for development"
  helpUsage = """
%prog <branchname> [<project>...]
"""
  helpDescription = """
The '%prog' command checks out an existing branch that was previously
created by 'repo start'.

The command is equivalent to:

  repo forall [<project>...] -c git checkout <branchname>
"""

  """
  'repo checkout'命令中'checkout'操作的主函数。
  """
  def Execute(self, opt, args):
    if not args:
      self.Usage()

    nb = args[0]
    err = []
    success = []
    all_projects = self.GetProjects(args[1:])

    """
    根据传入的[<project>...]选项调用GetProjects()进行projects筛选，返回满足条件的projects，结果存放到all_projects中。
    对满足条件的all_projects进行遍历，逐个调用CheckoutBranch(nb)操作
    """
    pm = Progress('Checkout %s' % nb, len(all_projects))
    for project in all_projects:
      pm.update()

      status = project.CheckoutBranch(nb)
      if status is not None:
        if status:
          success.append(project)
        else:
          err.append(project)
    pm.end()

    """
    前面checkout操作得到2个项目列表，操作成功的project存入success列表，失败的project存到err列表

    如果err列表不为空，则说明有project进行checkout操作失败，显示操作失败的project。
    如果success列表为空，说明checkout的分支在当前的工作目录下不存在。
    """
    if err:
      for p in err:
        print("error: %s/: cannot checkout %s" % (p.relpath, nb),
              file=sys.stderr)
      sys.exit(1)
    elif not success:
      print('error: no project has branch %s' % nb, file=sys.stderr)
      sys.exit(1)
