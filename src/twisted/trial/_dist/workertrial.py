# -*- test-case-name: twisted.trial._dist.test.test_workertrial -*-
#
# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.

"""
Implementation of C{AMP} worker commands, and main executable entry point for
the workers.

@since: 12.3
"""

import sys
import os
import errno
from io import StringIO, TextIOWrapper


def _setupPath(environ):
    """
    Override C{sys.path} with what the parent passed in B{TRIAL_PYTHONPATH}.

    @see: twisted.trial._dist.disttrial.DistTrialRunner.launchWorkerProcesses
    """
    if "TRIAL_PYTHONPATH" in environ:
        sys.path[:] = environ["TRIAL_PYTHONPATH"].split(os.pathsep)


_setupPath(os.environ)


from twisted.internet.protocol import FileWrapper
from twisted.python.log import startLoggingWithObserver, textFromEventDict
from twisted.trial._dist.options import WorkerOptions
from twisted.trial._dist import _WORKER_AMP_STDIN, _WORKER_AMP_STDOUT


class WorkerLogObserver:
    """
    A log observer that forward its output to a C{AMP} protocol.
    """

    def __init__(self, protocol):
        """
        @param protocol: a connected C{AMP} protocol instance.
        @type protocol: C{AMP}
        """
        self.protocol = protocol

    def emit(self, eventDict):
        """
        Produce a log output.
        """
        from twisted.trial._dist import managercommands

        text = textFromEventDict(eventDict)
        if text is None:
            return
        self.protocol.callRemote(managercommands.TestWrite, out=text)


class WorkerStdouObserver(StringIO):
    """
    Handles the forwarding of the sub-process stdout to the managing AMP
    protocol.
    """

    def __init__(self, protocol):
        """
        @param protocol: a connected C{AMP} protocol instance.
        @type protocol: C{AMP}
        """
        self._protocol = protocol

    def write(self, data):
        from twisted.trial._dist import managercommands

        self._protocol.callRemote(managercommands.TestWrite, out=data)


class IOWrapper(TextIOWrapper):
    encoding = "utf-8"

    def __init__(self, fd):
        super().__init__(fd)
        self.encoding = "utf-8"


class WorkerStdin(StringIO):
    """
    Placeholder to raise an error is a test tries to use the standard input.
    """

    def read(self):
        raise AssertionError("Reading from stdin inside a test is not supported.")


def main(_fdopen=os.fdopen):
    """
    Main function to be run if __name__ == "__main__".

    This is called in the sub-process spawned by L{DistTrialRunner}.

    @param _fdopen: If specified, the function to use in place of C{os.fdopen}.
    @type _fdopen: C{callable}
    """
    # Prepare the subprocess communication pipes as soon as possible.
    os.dup2(0, _WORKER_AMP_STDIN, inheritable=False)
    os.dup2(1, _WORKER_AMP_STDOUT, inheritable=False)
    r, w = os.pipe()
    os.dup2(r, 0, inheritable=True)
    os.dup2(w, 1, inheritable=True)

    sys.stdin = IOWrapper(os.fdopen(0, "r"))
    sys.stdout = IOWrapper(os.fdopen(1, "w"))

    config = WorkerOptions()
    config.parseOptions()

    from twisted.trial._dist.worker import WorkerProtocol

    workerProtocol = WorkerProtocol(config["force-gc"])

    protocolIn = _fdopen(_WORKER_AMP_STDIN, "rb")
    protocolOut = _fdopen(_WORKER_AMP_STDOUT, "wb")
    workerProtocol.makeConnection(FileWrapper(protocolOut))

    observer = WorkerLogObserver(workerProtocol)
    sys.stdin = WorkerStdin()
    sys.stdout = WorkerStdouObserver(workerProtocol)
    startLoggingWithObserver(observer.emit, False)

    while True:
        try:
            r = protocolIn.read(1)
        except OSError as e:
            if e.args[0] == errno.EINTR:
                continue
            else:
                raise
        if r == b"":
            break
        else:
            workerProtocol.dataReceived(r)
            protocolOut.flush()
            sys.stdout.flush()
            sys.stderr.flush()

    if config.tracer:
        sys.settrace(None)
        results = config.tracer.results()
        results.write_results(
            show_missing=True, summary=False, coverdir=config.coverdir().path
        )


if __name__ == "__main__":
    main()
