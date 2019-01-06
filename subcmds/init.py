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
import os
import platform
import re
import shutil
import sys

from pyversion import is_python3
if is_python3():
  import urllib.parse
else:
  import imp
  import urlparse
  urllib = imp.new_module('urllib')
  urllib.parse = urlparse

from color import Coloring
from command import InteractiveCommand, MirrorSafeCommand
from error import ManifestParseError
from project import SyncBuffer
from git_config import GitConfig
from git_command import git_require, MIN_GIT_VERSION

"""
$ repo help init

Summary
-------
Initialize repo in the current directory

Usage: repo init [options]

Options:
  -h, --help            show this help message and exit

  Logging options:
    -q, --quiet         be quiet

  Manifest options:
    -u URL, --manifest-url=URL
                        manifest repository location
    -b REVISION, --manifest-branch=REVISION
                        manifest branch or revision
    -m NAME.xml, --manifest-name=NAME.xml
                        initial manifest file
    --mirror            create a replica of the remote repositories rather
                        than a client working directory
    --reference=DIR     location of mirror directory
    --depth=DEPTH       create a shallow clone with given depth; see git clone
    --archive           checkout an archive instead of a git repository for
                        each project. See git archive.
    -g GROUP, --groups=GROUP
                        restrict manifest projects to ones with specified
                        group(s) [default|all|G1,G2,G3|G4,-G5,-G6]
    -p PLATFORM, --platform=PLATFORM
                        restrict manifest projects to ones with a specified
                        platform group [auto|all|none|linux|darwin|...]
    --no-clone-bundle   disable use of /clone.bundle on HTTP/HTTPS

  repo Version options:
    --repo-url=URL      repo repository location
    --repo-branch=REVISION
                        repo branch or revision
    --no-repo-verify    do not verify repo source code

  Other options:
    --config-name       Always prompt for name/e-mail

Description
-----------
The 'repo init' command is run once to install and initialize repo. The
latest repo source code and manifest collection is downloaded from the
server and is installed in the .repo/ directory in the current working
directory.

The optional -b argument can be used to select the manifest branch to
checkout and use. If no branch is specified, master is assumed.

The optional -m argument can be used to specify an alternate manifest to
be used. If no manifest is specified, the manifest default.xml will be
used.

The --reference option can be used to point to a directory that has the
content of a --mirror sync. This will make the working directory use as
much data as possible from the local reference directory when fetching
from the server. This will make the sync go a lot faster by reducing
data traffic on the network.

The --no-clone-bundle option disables any attempt to use
$URL/clone.bundle to bootstrap a new Git repository from a resumeable
bundle file on a content delivery network. This may be necessary if
there are problems with the local Python HTTP client or proxy
configuration, but the Git binary works.

Switching Manifest Branches
---------------------------
To switch to another manifest branch, `repo init -b otherbranch` may be
used in an existing client. However, as this only updates the manifest,
a subsequent `repo sync` (or `repo sync -d`) is necessary to update the
working directory files.
"""

"""
这里的Init类继承自InteractiveCommand和MirrorSafeCommand，说明终端有交互，同时可以在镜像仓库上调用。
"""
class Init(InteractiveCommand, MirrorSafeCommand):
  common = True
  helpSummary = "Initialize repo in the current directory"
  helpUsage = """
%prog [options]
"""
  helpDescription = """
The '%prog' command is run once to install and initialize repo.
The latest repo source code and manifest collection is downloaded
from the server and is installed in the .repo/ directory in the
current working directory.

The optional -b argument can be used to select the manifest branch
to checkout and use.  If no branch is specified, master is assumed.

The optional -m argument can be used to specify an alternate manifest
to be used. If no manifest is specified, the manifest default.xml
will be used.

The --reference option can be used to point to a directory that
has the content of a --mirror sync. This will make the working
directory use as much data as possible from the local reference
directory when fetching from the server. This will make the sync
go a lot faster by reducing data traffic on the network.

The --no-clone-bundle option disables any attempt to use
$URL/clone.bundle to bootstrap a new Git repository from a
resumeable bundle file on a content delivery network. This
may be necessary if there are problems with the local Python
HTTP client or proxy configuration, but the Git binary works.

Switching Manifest Branches
---------------------------

To switch to another manifest branch, `repo init -b otherbranch`
may be used in an existing client.  However, as this only updates the
manifest, a subsequent `repo sync` (or `repo sync -d`) is necessary
to update the working directory files.
"""

  """
  定义'repo init'命令的参数选项
  """
  def _Options(self, p):
    # Logging
    g = p.add_option_group('Logging options')
    g.add_option('-q', '--quiet',
                 dest="quiet", action="store_true", default=False,
                 help="be quiet")

    # Manifest
    g = p.add_option_group('Manifest options')
    g.add_option('-u', '--manifest-url',
                 dest='manifest_url',
                 help='manifest repository location', metavar='URL')
    g.add_option('-b', '--manifest-branch',
                 dest='manifest_branch',
                 help='manifest branch or revision', metavar='REVISION')
    g.add_option('-m', '--manifest-name',
                 dest='manifest_name', default='default.xml',
                 help='initial manifest file', metavar='NAME.xml')
    g.add_option('--mirror',
                 dest='mirror', action='store_true',
                 help='create a replica of the remote repositories '
                      'rather than a client working directory')
    g.add_option('--reference',
                 dest='reference',
                 help='location of mirror directory', metavar='DIR')
    g.add_option('--depth', type='int', default=None,
                 dest='depth',
                 help='create a shallow clone with given depth; see git clone')
    g.add_option('--archive',
                 dest='archive', action='store_true',
                 help='checkout an archive instead of a git repository for '
                      'each project. See git archive.')
    g.add_option('-g', '--groups',
                 dest='groups', default='default',
                 help='restrict manifest projects to ones with specified '
                      'group(s) [default|all|G1,G2,G3|G4,-G5,-G6]',
                 metavar='GROUP')
    g.add_option('-p', '--platform',
                 dest='platform', default='auto',
                 help='restrict manifest projects to ones with a specified '
                      'platform group [auto|all|none|linux|darwin|...]',
                 metavar='PLATFORM')
    g.add_option('--no-clone-bundle',
                 dest='no_clone_bundle', action='store_true',
                 help='disable use of /clone.bundle on HTTP/HTTPS')

    # Tool
    g = p.add_option_group('repo Version options')
    g.add_option('--repo-url',
                 dest='repo_url',
                 help='repo repository location', metavar='URL')
    g.add_option('--repo-branch',
                 dest='repo_branch',
                 help='repo branch or revision', metavar='REVISION')
    g.add_option('--no-repo-verify',
                 dest='no_repo_verify', action='store_true',
                 help='do not verify repo source code')

    # Other
    g = p.add_option_group('Other options')
    g.add_option('--config-name',
                 dest='config_name', action="store_true", default=False,
                 help='Always prompt for name/e-mail')

  """
  'repo init'命令执行时需要检查的环境变量
  包括:
  - 'manifest_url'参数的'REPO_MANIFEST_URL'
  - 'reference'参数的'REPO_MIRROR_LOCATION'
  """
  def _RegisteredEnvironmentOptions(self):
    return {'REPO_MANIFEST_URL': 'manifest_url',
            'REPO_MIRROR_LOCATION': 'reference'}

  """
  根据opt中保存的选项同步Manifest库，是整个init操作的脚本的核心。

  额，简直就是废话，'init'操作的核心不就是为了同步manifest库嘛？
  """
  def _SyncManifest(self, opt):
    m = self.manifest.manifestProject
    """
    Exists属性会检查git库的gitdir('.repo/manifests.git')和objdir('.repo/manifests.git')是否存在，如果二者都存在才确认当前git库存在

    如果不存在，那说明当前需要新建一个manifest库，设置is_new = True
    """
    is_new = not m.Exists

    """
    对于目录'.repo/manifests/.git':
    - 不存在，则需要先通过manifest_url地址下载manifest库
      - 本地镜像mirror路径
        如果有指定本地的镜像reference的路径
      - opt.manifest_branch
        如果有指定manifest库的分支，则切换到相应分支，否则使用默认的'refs/heads/master'分支。
    - 存在，则说明之前已经下载过manifest库，现在只需要切换到指定分支就好。
    """
    if is_new:
      """
      新建manifest库时需要通过'-u URL, --manifest-url=URL'选项指定manifest库的地址
      如果没有指定，显示警告信息。
      """
      if not opt.manifest_url:
        print('fatal: manifest url (-u) is required.', file=sys.stderr)
        sys.exit(1)

      """
      检查用户级别的配置中，是否存在manifest库的替换地址

      奇怪的是，这个转换的地址在这里并没有被使用。那是在哪里使用的呢？
      实际上，在git下载代码时会自动使用insteadof的设置进行替换。
      """
      if not opt.quiet:
        print('Get %s' % GitConfig.ForUser().UrlInsteadOf(opt.manifest_url),
              file=sys.stderr)

      # The manifest project object doesn't keep track of the path on the
      # server where this git is located, so let's save that here.
      """
      在使用了'--reference'参数的情况下，检查manifest库位于mirror镜像上的地址
      """
      mirrored_manifest_git = None
      if opt.reference:
        """
        urlparse示例：
        >>> urlparse.urlparse('https://gerrit.googlesource.com/git-repo')
        ParseResult(scheme='https',
                    netloc='gerrit.googlesource.com',
                    path='/git-repo',
                    params='',
                    query='',
                    fragment='')
        所以这里需要对结果path='/git-repo'进行处理，取path[1:]并和opt.reference组成本地路径。

        例如命令：'repo init -u https://aosp.tuna.tsinghua.edu.cn/platform/manifest -b android-4.0.1_r1 --reference=/aosp/mirror'
        这里：manifest_url = 'https://aosp.tuna.tsinghua.edu.cn/platform/manifest', reference = '/aosp/mirror'
        因此:
            manifest_git_path = 'platform/manifest'

        构造基于mirror的镜像地址：
        mirrored_manifest_git = opt.reference + manifest_git_path = '/aosp/mirror/platform/manifest.git'

        如果基于mirror镜像地址下manifest_git_path路径的manifest库不存在，则尝试非镜像方式的路径('.repo/manifests.git')：
        mirrored_manifest_git = opt.reference + '.repo/manifests.git' = '/aosp/mirror/.repo/manifests.git'
        """
        manifest_git_path = urllib.parse.urlparse(opt.manifest_url).path[1:]
        mirrored_manifest_git = os.path.join(opt.reference, manifest_git_path)
        if not mirrored_manifest_git.endswith(".git"):
          mirrored_manifest_git += ".git"
        if not os.path.exists(mirrored_manifest_git):
          mirrored_manifest_git = os.path.join(opt.reference + '/.repo/manifests.git')

      """
      使用mirror镜像的manifest库初始化当前的manifest库的git目录
      """
      m._InitGitDir(mirror_git=mirrored_manifest_git)

      """
      如果'repo init'带有'-b REVISION, --manifest-branch=REVISION'参数，则根据该参数设置manifest库的分支。
      如果不带有分支参数，则默认分支为'refs/heads/master'
      """
      if opt.manifest_branch:
        m.revisionExpr = opt.manifest_branch
      else:
        m.revisionExpr = 'refs/heads/master'
    else:
      """
      在manifest库已经存在的情况下执行同步操作

      例如，基于原来的master分支同步或同步到一个新的分支上:
      如果有指定新的分支'manifest_branch'，则设置分支引用参数用于同步;
      如果没有指定新的分支，则PreSync()操作会基于manifest库当前branch的merge属性设置分支引用参数(revisionExpr和revisionId)用于同步;
      """
      if opt.manifest_branch:
        m.revisionExpr = opt.manifest_branch
      else:
        m.PreSync()

    """
    如果'repo init'带有'--manifest-url=url'参数，则使用url参数替代manifest库的'.git/config'中名为$name的remote源地址。

    主要包含以下操作：(这里的$name为'origin')
    1. 获取'.git/config'中名为$name的remote源对象
    2. 将remote源的url设置为参数的'manifest_url'
    3. 更新manifest库的fetch设置，如 fetch = '+refs/heads/*:refs/remotes/origin/*'
    4. 将remote源的设置保存回'.git/config'文件
    """
    if opt.manifest_url:
      r = m.GetRemote(m.remote.name)
      r.url = opt.manifest_url
      r.ResetFetch()
      r.Save()

    """
    对groups选项参数进行处理，默认为'default'，所以默认groups = ['default']

    -g GROUP, --groups=GROUP
                        restrict manifest projects to ones with specified
                        group(s) [default|all|G1,G2,G3|G4,-G5,-G6]
    """
    groups = re.split(r'[,\s]+', opt.groups)
    all_platforms = ['linux', 'darwin', 'windows']
    platformize = lambda x: 'platform-' + x

    """
    对platform参数进行处理，默认为auto，此时实际上是运行时通过platform.system()返回值判断系统是linux, darwin还是其他。

    如果opt.platform == 'auto'，对于Ubuntu系统，platform.system()为'Linux'，则groups = ['default', 'platform-linux']

    -p PLATFORM, --platform=PLATFORM
                        restrict manifest projects to ones with a specified
                        platform group [auto|all|none|linux|darwin|...]
    """
    if opt.platform == 'auto':
      if (not opt.mirror and
          not m.config.GetString('repo.mirror') == 'true'):
        groups.append(platformize(platform.system().lower()))
    elif opt.platform == 'all':
      groups.extend(map(platformize, all_platforms))
    elif opt.platform in all_platforms:
      groups.append(platformize(opt.platform))
    elif opt.platform != 'none':
      print('fatal: invalid platform flag', file=sys.stderr)
      sys.exit(1)

    """
    将解析得到的groups生成groupstr字符串，并写入到'.git/config'文件中
    对于linux下，默认groups = ['default', 'platform-linux']，因此最终groupstr = None

    命令: 'git config --file .repo/manifests/.git/config manifest.groups $groupstr'

    例如'group=G1, platform=all'的情况：
    $ repo init -g G1 -p all
    $ cat .repo/manifests/.git/config
    ...
    [manifest]
      groups = G1,platform-linux,platform-darwin,platform-windows

    可见，这里的manifest.groups被设置为'G1,platform-linux,platform-darwin,platform-windows'
    """
    groups = [x for x in groups if x]
    groupstr = ','.join(groups)
    if opt.platform == 'auto' and groupstr == 'default,platform-' + platform.system().lower():
      groupstr = None
    m.config.SetString('manifest.groups', groupstr)

    """
    如果'repo init'带有'--reference=DIR'参数，则向manifest的config文件写入'repo.reference=DIR'，
    命令：'git config --file .repo/manifests/.git/config repo.reference $DIR'
    """
    if opt.reference:
      m.config.SetString('repo.reference', opt.reference)

    """
    如果'repo init'带有'--archive'参数，则向manifest的config文件写入'repo.archive=true'
    命令：'git config --file .repo/manifests/.git/config repo.archive true'
    """
    if opt.archive:
      if is_new:
        m.config.SetString('repo.archive', 'true')
      else:
        print('fatal: --archive is only supported when initializing a new '
              'workspace.', file=sys.stderr)
        print('Either delete the .repo folder in this workspace, or initialize '
              'in another location.', file=sys.stderr)
        sys.exit(1)

    """
    如果'repo init'带有'--mirror'参数，则向manifest的config文件写入'repo.mirror=true'
    命令：'git config --file .repo/manifests/.git/config repo.mirror true'

    特别注意的是，'--mirror'参数只能在第一次运行仓库初始化命令时执行。
    """
    if opt.mirror:
      if is_new:
        m.config.SetString('repo.mirror', 'true')
      else:
        print('fatal: --mirror is only supported when initializing a new '
              'workspace.', file=sys.stderr)
        print('Either delete the .repo folder in this workspace, or initialize '
              'in another location.', file=sys.stderr)
        sys.exit(1)

    """
    同步分为两部分：Sync_NetworkHalf和Sync_LocalHarf。
    """
    if not m.Sync_NetworkHalf(is_new=is_new, quiet=opt.quiet,
        clone_bundle=not opt.no_clone_bundle):
      r = m.GetRemote(m.remote.name)
      print('fatal: cannot obtain manifest %s' % r.url, file=sys.stderr)

      # Better delete the manifest git dir if we created it; otherwise next
      # time (when user fixes problems) we won't go through the "is_new" logic.
      if is_new:
        shutil.rmtree(m.gitdir)
      sys.exit(1)

    """
    如果'repo init'带有'--manifest-branch'参数，则调用MetaBranchSwitch()删除'refs/heads/default'引用。
    """
    if opt.manifest_branch:
      m.MetaBranchSwitch()

    syncbuf = SyncBuffer(m.config)
    m.Sync_LocalHalf(syncbuf)
    syncbuf.Finish()

    """
    如果是同步一个新仓库(is_new=True)，或manifest库的当前分支为空，则切换到manifest库的'default'分支上去。
    如果'default'分支不存在，则会创建一个名为'default'的分支。
    """
    if is_new or m.CurrentBranch is None:
      if not m.StartBranch('default'):
        print('fatal: cannot create default in manifest', file=sys.stderr)
        sys.exit(1)

  """
  将'manifests'目录下的文件'$name'文件链接到'.repo/manifest.xml'，即：
  '.repo/manifests/$name' --> '.repo/manifest.xml'
  """
  def _LinkManifest(self, name):
    if not name:
      print('fatal: manifest name (-m) is required.', file=sys.stderr)
      sys.exit(1)

    try:
      self.manifest.Link(name)
    except ManifestParseError as e:
      print("fatal: manifest '%s' not available" % name, file=sys.stderr)
      print('fatal: %s' % str(e), file=sys.stderr)
      sys.exit(1)

  """
  格式化显示提示信息，并读取终端输入

  如格式：'Your Name  [Rocky Gu]:'
  """
  def _Prompt(self, prompt, value):
    sys.stdout.write('%-10s [%s]: ' % (prompt, value))
    a = sys.stdin.readline().strip()
    if a == '':
      return value
    return a

  """
  检查manifest仓库的config中是否包含'user.name'和'user.email'设置，并显示相应的提示信息。

  默认使用全局(即用户级别的配置'~/.gitconfig')来更新仓库级别('.git/config')的配置信息
  """
  def _ShouldConfigureUser(self):
    """
    读取manifest库对应的全局config(用户级别'~/.gitconfig')和仓库级别的config('.git/config')

    先后检查仓库级别的设置和全局设置中是否包含'user.name'和'user.email'设置:
    - 如果都没有，说明需要进行设置。
    - 如果仓库级别('.git/config')没有设置，但全局('~/.gitconfig')有设置，则使用全局的设置来更新仓库级别的设置。

    最后显示'--config-name'选项的提示信息
    """
    gc = self.manifest.globalConfig
    mp = self.manifest.manifestProject

    # If we don't have local settings, get from global.
    if not mp.config.Has('user.name') or not mp.config.Has('user.email'):
      if not gc.Has('user.name') or not gc.Has('user.email'):
        return True

      mp.config.SetString('user.name', gc.GetString('user.name'))
      mp.config.SetString('user.email', gc.GetString('user.email'))

    """
    显示类似如下的提示信息：

    Your identity is: Rocky Gu <rocky.gu@broadcom.com>
    If you want to change this, please re-run 'repo init' with --config-name
    """
    print()
    print('Your identity is: %s <%s>' % (mp.config.GetString('user.name'),
                                         mp.config.GetString('user.email')))
    print('If you want to change this, please re-run \'repo init\' with --config-name')
    return False

  """
  提示用户输入并确认Name和Email信息
  """
  def _ConfigureUser(self):
    """
    提示用户输入并确认Name和Email信息，如：
    $ repo init --config-name

    Your Name  [Rocky Gu]:
    Your Email [rocky.gu@broadcom.com]:

    Your identity is: Rocky Gu <rocky.gu@broadcom.com>
    is this correct [y/N]? y

    repo has been initialized in /public/aosp-latest

    如果用户新输入的Name和Email跟之前的设置不一样，则调用以下命令进行设置：
    'git config --file .repo/manifests/.git/config user.name $name'
    'git config --file .repo/manifests/.git/config user.email $email'
    """
    mp = self.manifest.manifestProject

    while True:
      print()
      name  = self._Prompt('Your Name', mp.UserName)
      email = self._Prompt('Your Email', mp.UserEmail)

      print()
      print('Your identity is: %s <%s>' % (name, email))
      sys.stdout.write('is this correct [y/N]? ')
      a = sys.stdin.readline().strip().lower()
      if a in ('yes', 'y', 't', 'true'):
        break

    if name != mp.UserName:
      mp.config.SetString('user.name', name)
    if email != mp.UserEmail:
      mp.config.SetString('user.email', email)

  """
  检查gc配置中是否包含颜色相关的'color.ui', 'color.diff'或'color.status'设置项。
  """
  def _HasColorSet(self, gc):
    for n in ['ui', 'diff', 'status']:
      if gc.Has('color.%s' % n):
        return True
    return False

  """
  检查全局配置文件'~/.gitconfig'并设置'color.ui=auto'，确保'repo diff'和'repo status'操作时会有颜色显示不同状态。
  """
  def _ConfigureColor(self):
    """
    检查全局设置globalConfig(即用户级别'~/.gitconfig')中是否包含颜色相关的'color.ui', 'color.diff'或'color.status'设置项。
    如果有相关设置，则不做任何操作直接返回。
    如果没有相关设置，则提示如下信息：
    $ repo init -u https://aosp.tuna.tsinghua.edu.cn/platform/manifest
    ...

    Testing colorized output (for 'repo diff', 'repo status'):
      black    red      green    yellow   blue     magenta   cyan     white
      bold     dim      ul       reverse
    Enable color display in this user account (y/N)? y

    repo has been initialized in /public/aosp-latest

    在用户做出确认后，设置'~/.gitconfig'文件：
    'git config --file ~/.gitconfig --replace-all color.ui auto'
    $ cat ~/.gitconfig
    ...
    [color]
      ui = auto
    ...
    """
    gc = self.manifest.globalConfig
    if self._HasColorSet(gc):
      return

    class _Test(Coloring):
      def __init__(self):
        Coloring.__init__(self, gc, 'test color display')
        self._on = True
    out = _Test()

    """
    按照各种颜色进行显示
    """
    print()
    print("Testing colorized output (for 'repo diff', 'repo status'):")

    for c in ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan']:
      out.write(' ')
      out.printer(fg=c)(' %-6s ', c)
    out.write(' ')
    out.printer(fg='white', bg='black')(' %s ' % 'white')
    out.nl()

    for c in ['bold', 'dim', 'ul', 'reverse']:
      out.write(' ')
      out.printer(fg='black', attr=c)(' %-6s ', c)
    out.nl()

    """
    确认用户输入并执行命令：'git config --file ~/.gitconfig --replace-all color.ui auto'
    """
    sys.stdout.write('Enable color display in this user account (y/N)? ')
    a = sys.stdin.readline().strip().lower()
    if a in ('y', 'yes', 't', 'true', 'on'):
      gc.SetString('color.ui', 'auto')

  """
  如果'repo init'有附加'--depth'参数，则将该参数写入到manifest的config文件'repo.depth'设置中。
  """
  def _ConfigureDepth(self, opt):
    """Configure the depth we'll sync down.

    Args:
      opt: Options from optparse.  We care about opt.depth.
    """
    # Opt.depth will be non-None if user actually passed --depth to repo init.
    """
    只有在'--depth'参数有设置时才进行后续操作，将其写入到manifest仓库级别的配置项'repo.depth'中。
    如：
    $ repo init --depth=2
    $ cat .repo/manifests/.git/config
    ...
    [repo]
      depth = 2
    ...
    """
    if opt.depth is not None:
      if opt.depth > 0:
        # Positive values will set the depth.
        depth = str(opt.depth)
      else:
        # Negative numbers will clear the depth; passing None to SetString
        # will do that.
        depth = None

      # We store the depth in the main manifest project.
      """
      执行命令：'.repo/manifests$ git config --file .git/config repo.depth $depth'
      """
      self.manifest.manifestProject.config.SetString('repo.depth', depth)

  """
  显示'repo init'完成的提示消息
  """
  def _DisplayResult(self):
    if self.manifest.IsMirror:
      init_type = 'mirror '
    else:
      init_type = ''

    """
    默认完成后会提示：
    /public/aosp-latest$ repo init ...
    ...
    repo has been initialized in /public/aosp-latest

    在错误的位置初始化时会提示：
    /public/aosp-latest/.repo$ repo init ...
    ...
    repo has been initialized in /public/aosp-latest
    If this is not the directory in which you want to initialize repo, please run:
       rm -r /public/aosp-latest/.repo
    and try again.
    """
    print()
    print('repo %shas been initialized in %s'
          % (init_type, self.manifest.topdir))

    current_dir = os.getcwd()
    if current_dir != self.manifest.topdir:
      print('If this is not the directory in which you want to initialize '
            'repo, please run:')
      print('   rm -r %s/.repo' % self.manifest.topdir)
      print('and try again.')

  """
  'repo init'中'init'操作的主函数。
  """
  def Execute(self, opt, args):
    git_require(MIN_GIT_VERSION, fail=True)

    """
    如果'repo init'提供了'--reference=DIR'选项：
    --reference=DIR     location of mirror directory

    即opt.reference用于指定本地镜像的目录
    """
    if opt.reference:
      opt.reference = os.path.expanduser(opt.reference)

    # Check this here, else manifest will be tagged "not new" and init won't be
    # possible anymore without removing the .repo/manifests directory.
    """
    'repo init'命令的'--archive'和'--reference'不能同时生效：
    --reference=DIR     location of mirror directory
    ...
    --archive           checkout an archive instead of a git repository for
                        each project. See git archive.
    """
    if opt.archive and opt.mirror:
      print('fatal: --mirror and --archive cannot be used together.',
            file=sys.stderr)
      sys.exit(1)

    """
    同步manifest

    'repo init'命令的'-m'/'--manifest-name'选项用于指定使用的manifest文件
    -m NAME.xml, --manifest-name=NAME.xml
                        initial manifest file

    同步完成后将manifest_name指定的文件链接到'.repo/manifest.xml'，即：
    '.repo/manifests/$manifest_name' --> '.repo/manifest.xml'
    """
    self._SyncManifest(opt)
    self._LinkManifest(opt.manifest_name)

    """
    如果当前不是初始化mirror镜像，则：
    1. 往'.repo/manifests/.git/config'设置'user.name'和'user.email';
    2. 往'~/.gitconfig'设置'color.ui=auto';

    3. 根据'--depth'参数，往'.repo/manifests/.git/config'设置'repo.depth=$depth'

    最后显示'repo init'操作结束的提示信息:
    repo has been initialized in ...
    """
    if os.isatty(0) and os.isatty(1) and not self.manifest.IsMirror:
      if opt.config_name or self._ShouldConfigureUser():
        self._ConfigureUser()
      self._ConfigureColor()

    self._ConfigureDepth(opt)

    self._DisplayResult()
