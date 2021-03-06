import sublime
import sublime_plugin
import sys
import os
import logging
from unittest import TextTestRunner, TestSuite
from .core import (
    TestLoader,
    DeferringTextTestRunner,
    LegacyDeferringTextTestRunner,
    DeferrableTestCase
)
from .mixin import UnitTestingMixin
from .const import DONE_MESSAGE
from .utils import ProgressBar, StdioSplitter
import threading


class UnitTestingCommand(sublime_plugin.ApplicationCommand, UnitTestingMixin):

    def run(self, package=None, **kwargs):
        if not package:
            self.prompt_package(lambda x: self.run(x, **kwargs))
            return

        package, pattern = self.input_parser(package)
        if pattern is not None:
            # kwargs have the highest precedence when evaluating the settings,
            # so we sure don't want to pass `None` down
            kwargs['pattern'] = pattern

        settings = self.load_unittesting_settings(package, kwargs)
        stream = self.load_stream(package, settings)

        if settings["async"]:
            threading.Thread(target=lambda: self.unit_testing(stream, package, settings)).start()
        else:
            self.unit_testing(stream, package, settings)

    def verify_testsuite(self, tests):
        for t in tests:
            if isinstance(t, TestSuite):
                self.verify_testsuite(t)
            if isinstance(t, DeferrableTestCase):
                raise Exception("DeferrableTestCase is used but `deferred` is `false`.")

    def unit_testing(self, stream, package, settings, cleanup_hooks=[]):
        if settings["capture_console"]:
            stdout = sys.stdout
            stderr = sys.stderr
            handler = logging.StreamHandler(stream)
            logging.root.addHandler(handler)

            sys.stdout = StdioSplitter(stdout, stream)
            sys.stderr = StdioSplitter(stderr, stream)

        testRunner = None
        progress_bar = ProgressBar("Testing %s" % package)
        progress_bar.start()

        try:
            # use custom loader which supports reloading modules
            self.remove_test_modules(package, settings["tests_dir"])
            loader = TestLoader(settings["deferred"])
            tests = loader.discover(os.path.join(
                sublime.packages_path(), package, settings["tests_dir"]), settings["pattern"]
            )
            # use deferred test runner or default test runner
            if settings["deferred"]:
                if settings["legacy_runner"]:
                    testRunner = LegacyDeferringTextTestRunner(stream, verbosity=settings["verbosity"])
                else:
                    testRunner = DeferringTextTestRunner(stream, verbosity=settings["verbosity"])
            else:
                self.verify_testsuite(tests)
                testRunner = TextTestRunner(stream, verbosity=settings["verbosity"])

            testRunner.run(tests)

        except Exception as e:
            if not stream.closed:
                stream.write("ERROR: %s\n" % e)
            # force clean up
            testRunner = None
        finally:
            def cleanup(status=0):
                if not settings["deferred"] or not testRunner or \
                        testRunner.finished or status > 600:
                    self.remove_test_modules(package, settings["tests_dir"])
                    progress_bar.stop()

                    for hook in cleanup_hooks:
                        hook()

                    if not hasattr(stream, 'window'):
                        # If it's an output panel don't print done message,
                        # because it's only required for CI test runs.
                        stream.write("\n")
                        stream.write(DONE_MESSAGE)

                    stream.close()

                    if settings["capture_console"]:
                        sys.stdout = stdout
                        sys.stderr = stderr
                        # remove stream set by logging.root.addHandler
                        logging.root.removeHandler(handler)
                else:
                    sublime.set_timeout(lambda: cleanup(status + 1), 500)

            cleanup()
