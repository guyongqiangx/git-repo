  1 #!/usr/bin/env python
  2 #
  3 # Copyright (C) 2008 The Android Open Source Project
  4 #
  5 # Licensed under the Apache License, Version 2.0 (the "License");
  6 # you may not use this file except in compliance with the License.
  7 # You may obtain a copy of the License at
  8 #
  9 #      http://www.apache.org/licenses/LICENSE-2.0
 10 #
 11 # Unless required by applicable law or agreed to in writing, software
 12 # distributed under the License is distributed on an "AS IS" BASIS,
 13 # WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 14 # See the License for the specific language governing permissions and
 15 # limitations under the License.
 16 
 17 from __future__ import print_function
 18 import getpass
 19 import imp
 20 import netrc
 21 import optparse
 22 import os
 23 import sys
 24 import time
 25 
 26 from pyversion import is_python3
 27 if is_python3():
 28   import urllib.request
 29 else:
 30   import urllib2
 31   urllib = imp.new_module('urllib')
 32   urllib.request = urllib2
 33 
 34 try:
 35   import kerberos
 36 except ImportError:
 37   kerberos = None
 38 
 39 from color import SetDefaultColoring
 40 from trace import SetTrace
 41 from git_command import git, GitCommand
 42 from git_config import init_ssh, close_ssh
 43 from command import InteractiveCommand
 44 from command import MirrorSafeCommand
 45 from command import GitcAvailableCommand, GitcClientCommand
 46 from subcmds.version import Version
 47 from editor import Editor
 48 from error import DownloadError
 49 from error import InvalidProjectGroupsError
 50 from error import ManifestInvalidRevisionError
 51 from error import ManifestParseError
 52 from error import NoManifestException
 53 from error import NoSuchProjectError
 54 from error import RepoChangedException
 55 import gitc_utils
 56 from manifest_xml import GitcManifest, XmlManifest
 57 from pager import RunPager
 58 from wrapper import WrapperPath, Wrapper
 59 
 60 from subcmds import all_commands
 61 
 62 if not is_python3():
 63   # pylint:disable=W0622
 64   input = raw_input
 65   # pylint:enable=W0622
 66 
 67 global_options = optparse.OptionParser(
 68                  usage="repo [-p|--paginate|--no-pager] COMMAND [ARGS]"
 69                  )
 70 global_options.add_option('-p', '--paginate',
 71                           dest='pager', action='store_true',
 72                           help='display command output in the pager')
 73 global_options.add_option('--no-pager',
 74                           dest='no_pager', action='store_true',
 75                           help='disable the pager')
 76 global_options.add_option('--color',
 77                           choices=('auto', 'always', 'never'), default=None,
 78                           help='control color usage: auto, always, never')
 79 global_options.add_option('--trace',
 80                           dest='trace', action='store_true',
 81                           help='trace git command execution')
 82 global_options.add_option('--time',
 83                           dest='time', action='store_true',
 84                           help='time repo command execution')
 85 global_options.add_option('--version',
 86                           dest='show_version', action='store_true',
 87                           help='display this version of repo')
 88 
 89 class _Repo(object):
 90   def __init__(self, repodir):
 91     self.repodir = repodir
 92     self.commands = all_commands
 93     # add 'branch' as an alias for 'branches'
 94     all_commands['branch'] = all_commands['branches']
 95 
 96   def _Run(self, argv):
 97     result = 0
 98     name = None
 99     glob = []
100 
101     for i in range(len(argv)):
102       if not argv[i].startswith('-'):
103         name = argv[i]
104         if i > 0:
105           glob = argv[:i]
106         argv = argv[i + 1:]
107         break
108     if not name:
109       glob = argv
110       name = 'help'
111       argv = []
112     gopts, _gargs = global_options.parse_args(glob)
113 
114     if gopts.trace:
115       SetTrace()
116     if gopts.show_version:
117       if name == 'help':
118         name = 'version'
119       else:
120         print('fatal: invalid usage of --version', file=sys.stderr)
121         return 1
122 
123     SetDefaultColoring(gopts.color)
124 
125     try:
126       cmd = self.commands[name]
127     except KeyError:
128       print("repo: '%s' is not a repo command.  See 'repo help'." % name,
129             file=sys.stderr)
130       return 1
131 
132     cmd.repodir = self.repodir
133     cmd.manifest = XmlManifest(cmd.repodir)
134     cmd.gitc_manifest = None
135     gitc_client_name = gitc_utils.parse_clientdir(os.getcwd())
136     if gitc_client_name:
137       cmd.gitc_manifest = GitcManifest(cmd.repodir, gitc_client_name)
138       cmd.manifest.isGitcClient = True
139 
140     Editor.globalConfig = cmd.manifest.globalConfig
141 
142     if not isinstance(cmd, MirrorSafeCommand) and cmd.manifest.IsMirror:
143       print("fatal: '%s' requires a working directory" % name,
144             file=sys.stderr)
145       return 1
146 
147     if isinstance(cmd, GitcAvailableCommand) and not gitc_utils.get_gitc_manifest_dir():
148       print("fatal: '%s' requires GITC to be available" % name,
149             file=sys.stderr)
150       return 1
151 
152     if isinstance(cmd, GitcClientCommand) and not gitc_client_name:
153       print("fatal: '%s' requires a GITC client" % name,
154             file=sys.stderr)
155       return 1
156 
157     try:
158       copts, cargs = cmd.OptionParser.parse_args(argv)
159       copts = cmd.ReadEnvironmentOptions(copts)
160     except NoManifestException as e:
161       print('error: in `%s`: %s' % (' '.join([name] + argv), str(e)),
162         file=sys.stderr)
163       print('error: manifest missing or unreadable -- please run init',
164             file=sys.stderr)
165       return 1
166 
167     if not gopts.no_pager and not isinstance(cmd, InteractiveCommand):
168       config = cmd.manifest.globalConfig
169       if gopts.pager:
170         use_pager = True
171       else:
172         use_pager = config.GetBoolean('pager.%s' % name)
173         if use_pager is None:
174           use_pager = cmd.WantPager(copts)
175       if use_pager:
176         RunPager(config)
177 
178     start = time.time()
179     try:
180       result = cmd.Execute(copts, cargs)
181     except (DownloadError, ManifestInvalidRevisionError,
182         NoManifestException) as e:
183       print('error: in `%s`: %s' % (' '.join([name] + argv), str(e)),
184         file=sys.stderr)
185       if isinstance(e, NoManifestException):
186         print('error: manifest missing or unreadable -- please run init',
187               file=sys.stderr)
188       result = 1
189     except NoSuchProjectError as e:
190       if e.name:
191         print('error: project %s not found' % e.name, file=sys.stderr)
192       else:
193         print('error: no project in current directory', file=sys.stderr)
194       result = 1
195     except InvalidProjectGroupsError as e:
196       if e.name:
197         print('error: project group must be enabled for project %s' % e.name, file=sys.stderr)
198       else:
199         print('error: project group must be enabled for the project in the current directory', file=sys.stderr)
200       result = 1
201     finally:
202       elapsed = time.time() - start
203       hours, remainder = divmod(elapsed, 3600)
204       minutes, seconds = divmod(remainder, 60)
205       if gopts.time:
206         if hours == 0:
207           print('real\t%dm%.3fs' % (minutes, seconds), file=sys.stderr)
208         else:
209           print('real\t%dh%dm%.3fs' % (hours, minutes, seconds),
210                 file=sys.stderr)
211 
212     return result
213 
214 
215 def _MyRepoPath():
216   return os.path.dirname(__file__)
217 
218 
219 def _CheckWrapperVersion(ver, repo_path):
220   if not repo_path:
221     repo_path = '~/bin/repo'
222 
223   if not ver:
224     print('no --wrapper-version argument', file=sys.stderr)
225     sys.exit(1)
226 
227   exp = Wrapper().VERSION
228   ver = tuple(map(int, ver.split('.')))
229   if len(ver) == 1:
230     ver = (0, ver[0])
231 
232   exp_str = '.'.join(map(str, exp))
233   if exp[0] > ver[0] or ver < (0, 4):
234     print("""
235 !!! A new repo command (%5s) is available.    !!!
236 !!! You must upgrade before you can continue:   !!!
237 
238     cp %s %s
239 """ % (exp_str, WrapperPath(), repo_path), file=sys.stderr)
240     sys.exit(1)
241 
242   if exp > ver:
243     print("""
244 ... A new repo command (%5s) is available.
245 ... You should upgrade soon:
246 
247     cp %s %s
248 """ % (exp_str, WrapperPath(), repo_path), file=sys.stderr)
249 
250 def _CheckRepoDir(repo_dir):
251   if not repo_dir:
252     print('no --repo-dir argument', file=sys.stderr)
253     sys.exit(1)
254 
255 def _PruneOptions(argv, opt):
256   i = 0
257   while i < len(argv):
258     a = argv[i]
259     if a == '--':
260       break
261     if a.startswith('--'):
262       eq = a.find('=')
263       if eq > 0:
264         a = a[0:eq]
265     if not opt.has_option(a):
266       del argv[i]
267       continue
268     i += 1
269 
270 _user_agent = None
271 
272 def _UserAgent():
273   global _user_agent
274 
275   if _user_agent is None:
276     py_version = sys.version_info
277 
278     os_name = sys.platform
279     if os_name == 'linux2':
280       os_name = 'Linux'
281     elif os_name == 'win32':
282       os_name = 'Win32'
283     elif os_name == 'cygwin':
284       os_name = 'Cygwin'
285     elif os_name == 'darwin':
286       os_name = 'Darwin'
287 
288     p = GitCommand(
289       None, ['describe', 'HEAD'],
290       cwd = _MyRepoPath(),
291       capture_stdout = True)
292     if p.Wait() == 0:
293       repo_version = p.stdout
294       if len(repo_version) > 0 and repo_version[-1] == '\n':
295         repo_version = repo_version[0:-1]
296       if len(repo_version) > 0 and repo_version[0] == 'v':
297         repo_version = repo_version[1:]
298     else:
299       repo_version = 'unknown'
300 
301     _user_agent = 'git-repo/%s (%s) git/%s Python/%d.%d.%d' % (
302       repo_version,
303       os_name,
304       '.'.join(map(str, git.version_tuple())),
305       py_version[0], py_version[1], py_version[2])
306   return _user_agent
307 
308 class _UserAgentHandler(urllib.request.BaseHandler):
309   def http_request(self, req):
310     req.add_header('User-Agent', _UserAgent())
311     return req
312 
313   def https_request(self, req):
314     req.add_header('User-Agent', _UserAgent())
315     return req
316 
317 def _AddPasswordFromUserInput(handler, msg, req):
318   # If repo could not find auth info from netrc, try to get it from user input
319   url = req.get_full_url()
320   user, password = handler.passwd.find_user_password(None, url)
321   if user is None:
322     print(msg)
323     try:
324       user = input('User: ')
325       password = getpass.getpass()
326     except KeyboardInterrupt:
327       return
328     handler.passwd.add_password(None, url, user, password)
329 
330 class _BasicAuthHandler(urllib.request.HTTPBasicAuthHandler):
331   def http_error_401(self, req, fp, code, msg, headers):
332     _AddPasswordFromUserInput(self, msg, req)
333     return urllib.request.HTTPBasicAuthHandler.http_error_401(
334       self, req, fp, code, msg, headers)
335 
336   def http_error_auth_reqed(self, authreq, host, req, headers):
337     try:
338       old_add_header = req.add_header
339       def _add_header(name, val):
340         val = val.replace('\n', '')
341         old_add_header(name, val)
342       req.add_header = _add_header
343       return urllib.request.AbstractBasicAuthHandler.http_error_auth_reqed(
344         self, authreq, host, req, headers)
345     except:
346       reset = getattr(self, 'reset_retry_count', None)
347       if reset is not None:
348         reset()
349       elif getattr(self, 'retried', None):
350         self.retried = 0
351       raise
352 
353 class _DigestAuthHandler(urllib.request.HTTPDigestAuthHandler):
354   def http_error_401(self, req, fp, code, msg, headers):
355     _AddPasswordFromUserInput(self, msg, req)
356     return urllib.request.HTTPDigestAuthHandler.http_error_401(
357       self, req, fp, code, msg, headers)
358 
359   def http_error_auth_reqed(self, auth_header, host, req, headers):
360     try:
361       old_add_header = req.add_header
362       def _add_header(name, val):
363         val = val.replace('\n', '')
364         old_add_header(name, val)
365       req.add_header = _add_header
366       return urllib.request.AbstractDigestAuthHandler.http_error_auth_reqed(
367         self, auth_header, host, req, headers)
368     except:
369       reset = getattr(self, 'reset_retry_count', None)
370       if reset is not None:
371         reset()
372       elif getattr(self, 'retried', None):
373         self.retried = 0
374       raise
375 
376 class _KerberosAuthHandler(urllib.request.BaseHandler):
377   def __init__(self):
378     self.retried = 0
379     self.context = None
380     self.handler_order = urllib.request.BaseHandler.handler_order - 50
381 
382   def http_error_401(self, req, fp, code, msg, headers): # pylint:disable=unused-argument
383     host = req.get_host()
384     retry = self.http_error_auth_reqed('www-authenticate', host, req, headers)
385     return retry
386 
387   def http_error_auth_reqed(self, auth_header, host, req, headers):
388     try:
389       spn = "HTTP@%s" % host
390       authdata = self._negotiate_get_authdata(auth_header, headers)
391 
392       if self.retried > 3:
393         raise urllib.request.HTTPError(req.get_full_url(), 401,
394           "Negotiate auth failed", headers, None)
395       else:
396         self.retried += 1
397 
398       neghdr = self._negotiate_get_svctk(spn, authdata)
399       if neghdr is None:
400         return None
401 
402       req.add_unredirected_header('Authorization', neghdr)
403       response = self.parent.open(req)
404 
405       srvauth = self._negotiate_get_authdata(auth_header, response.info())
406       if self._validate_response(srvauth):
407         return response
408     except kerberos.GSSError:
409       return None
410     except:
411       self.reset_retry_count()
412       raise
413     finally:
414       self._clean_context()
415 
416   def reset_retry_count(self):
417     self.retried = 0
418 
419   def _negotiate_get_authdata(self, auth_header, headers):
420     authhdr = headers.get(auth_header, None)
421     if authhdr is not None:
422       for mech_tuple in authhdr.split(","):
423         mech, __, authdata = mech_tuple.strip().partition(" ")
424         if mech.lower() == "negotiate":
425           return authdata.strip()
426     return None
427 
428   def _negotiate_get_svctk(self, spn, authdata):
429     if authdata is None:
430       return None
431 
432     result, self.context = kerberos.authGSSClientInit(spn)
433     if result < kerberos.AUTH_GSS_COMPLETE:
434       return None
435 
436     result = kerberos.authGSSClientStep(self.context, authdata)
437     if result < kerberos.AUTH_GSS_CONTINUE:
438       return None
439 
440     response = kerberos.authGSSClientResponse(self.context)
441     return "Negotiate %s" % response
442 
443   def _validate_response(self, authdata):
444     if authdata is None:
445       return None
446     result = kerberos.authGSSClientStep(self.context, authdata)
447     if result == kerberos.AUTH_GSS_COMPLETE:
448       return True
449     return None
450 
451   def _clean_context(self):
452     if self.context is not None:
453       kerberos.authGSSClientClean(self.context)
454       self.context = None
455 
456 def init_http():
457   handlers = [_UserAgentHandler()]
458 
459   mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
460   try:
461     n = netrc.netrc()
462     for host in n.hosts:
463       p = n.hosts[host]
464       mgr.add_password(p[1], 'http://%s/'  % host, p[0], p[2])
465       mgr.add_password(p[1], 'https://%s/' % host, p[0], p[2])
466   except netrc.NetrcParseError:
467     pass
468   except IOError:
469     pass
470   handlers.append(_BasicAuthHandler(mgr))
471   handlers.append(_DigestAuthHandler(mgr))
472   if kerberos:
473     handlers.append(_KerberosAuthHandler())
474 
475   if 'http_proxy' in os.environ:
476     url = os.environ['http_proxy']
477     handlers.append(urllib.request.ProxyHandler({'http': url, 'https': url}))
478   if 'REPO_CURL_VERBOSE' in os.environ:
479     handlers.append(urllib.request.HTTPHandler(debuglevel=1))
480     handlers.append(urllib.request.HTTPSHandler(debuglevel=1))
481   urllib.request.install_opener(urllib.request.build_opener(*handlers))
482 
483 def _Main(argv):
484   result = 0
485 
486   opt = optparse.OptionParser(usage="repo wrapperinfo -- ...")
487   opt.add_option("--repo-dir", dest="repodir",
488                  help="path to .repo/")
489   opt.add_option("--wrapper-version", dest="wrapper_version",
490                  help="version of the wrapper script")
491   opt.add_option("--wrapper-path", dest="wrapper_path",
492                  help="location of the wrapper script")
493   _PruneOptions(argv, opt)
494   opt, argv = opt.parse_args(argv)
495 
496   _CheckWrapperVersion(opt.wrapper_version, opt.wrapper_path)
497   _CheckRepoDir(opt.repodir)
498 
499   Version.wrapper_version = opt.wrapper_version
500   Version.wrapper_path = opt.wrapper_path
501 
502   repo = _Repo(opt.repodir)
503   try:
504     try:
505       init_ssh()
506       init_http()
507       result = repo._Run(argv) or 0
508     finally:
509       close_ssh()
510   except KeyboardInterrupt:
511     print('aborted by user', file=sys.stderr)
512     result = 1
513   except ManifestParseError as mpe:
514     print('fatal: %s' % mpe, file=sys.stderr)
515     result = 1
516   except RepoChangedException as rce:
517     # If repo changed, re-exec ourselves.
518     #
519     argv = list(sys.argv)
520     argv.extend(rce.extra_args)
521     try:
522       os.execv(__file__, argv)
523     except OSError as e:
524       print('fatal: cannot restart repo after upgrade', file=sys.stderr)
525       print('fatal: %s' % e, file=sys.stderr)
526       result = 128
527 
528   sys.exit(result)
529 
530 if __name__ == '__main__':
531   _Main(sys.argv[1:])
