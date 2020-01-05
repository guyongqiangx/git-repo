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
import sys
from command import Command
from git_command import git
from progress import Progress

"""
$ repo help abandon

Summary
-------
Permanently abandon a development branch

Usage: repo abandon <branchname> [<project>...]

This subcommand permanently abandons a development branch by
deleting it (and all its history) from your local repository.

It is equivalent to "git branch -D <branchname>".

Options:
  -h, --help  show this help message and exit
"""
class Abandon(Command):
  common = True
  helpSummary = "Permanently abandon a development branch"
  helpUsage = """
%prog <branchname> [<project>...]

This subcommand permanently abandons a development branch by
deleting it (and all its history) from your local repository.

It is equivalent to "git branch -D <branchname>".
"""

  """
  'repo abandon'命令中'abandon'操作的主函数。
  """
  def Execute(self, opt, args):
    if not args:
      self.Usage()

    """
    nb(name of branch的缩写?)为命令中的<branchname>参数
    执行命令: 'git check-ref-format heads/$nb'
    使用传入的分支名称nb，构建名为heads/$nb的引用，通过'git check-ref-format heads/$nb'确保该引用(名为nb的分支)符合规范。
    参考: https://git-scm.com/docs/git-check-ref-format
    $ git check-ref-format @    # 这里'@'不符合引用规范
    $ echo $?
    1
    """
    nb = args[0]
    if not git.check_ref_format('heads/%s' % nb):
      print("error: '%s' is not a valid name" % nb, file=sys.stderr)
      sys.exit(1)

    nb = args[0]
    err = []
    success = []
    all_projects = self.GetProjects(args[1:])

    """
    根据传入的[<project>...]选项调用GetProjects()进行projects筛选，返回满足条件的projects，结果存放到all_projects中。
    对满足条件的all_projects进行遍历，逐个调用AbandonBranch(nb)操作
    """
    pm = Progress('Abandon %s' % nb, len(all_projects))
    for project in all_projects:
      pm.update()

      status = project.AbandonBranch(nb)
      if status is not None:
        if status:
          success.append(project)
        else:
          err.append(project)
    pm.end()

    """
    前面abandon操作得到2个项目列表，操作成功的project存入success列表，失败的project存到err列表

    如果err列表不为空，则说明有project进行abandon操作失败，显示操作失败的project。
    如果err和success列表都为空，说明abandon的分支在当前的工作目录下不存在。
    """
    if err:
      for p in err:
        print("error: %s/: cannot abandon %s" % (p.relpath, nb),
              file=sys.stderr)
      sys.exit(1)
    elif not success:
      print('error: no project has branch %s' % nb, file=sys.stderr)
      sys.exit(1)
    else:
      print('Abandoned in %d project(s):\n  %s'
            % (len(success), '\n  '.join(p.relpath for p in success)),
            file=sys.stderr)
