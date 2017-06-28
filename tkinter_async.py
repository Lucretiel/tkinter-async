# This file is part of tkinter_async.
#
# tkinter_async is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# tkinter_async is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with tkinter_async.  If not, see <http://www.gnu.org/licenses/>.
#
# Copyright 2015 Nathan West


import asyncio
import tkinter

from contextlib import contextmanager
from contextlib import suppress
from enum import Enum
from functools import partial
from functools import wraps
from itertools import count
from traceback import format_exc


_DONT_WAIT = tkinter._tkinter.DONT_WAIT


__all__ = []


def export(thing):
    '''
    Decorator to add things with a __name__ to __all__
    '''
    __all__.append(thing.__name__)
    return thing


# if tkinter.DEBUG_POPUPS is False, no debug popups will be created from
# `spawn` coroutines by default.
DEBUG_POPUPS = True


def getloop(func):
    '''
    This decorator modifies a function taking a `loop` kwarg to automatically
    look up the loop with asyncio.get_event_loop() when called, if it's None.
    '''
    @wraps(func)
    def getloop_wrapper(*args, loop=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        return func(*args, loop=loop, **kwargs)
    return wrapper


def popup_exc(master=None, title="Uncaught exception"):
    '''
    This function must *only* be called when handling an exception. When
    called, it creates and returns tkinter popup window with the exception
    traceback.
    '''
    window = tkinter.Toplevel(master)
    window.title(title)
    tkinter.Label(window, text=format_exc(), justify=tkinter.LEFT).grid()
    return window


@export
@contextmanager
def popup_uncaught(master=None, title="Uncaught exception", Base=Exception):
    '''
    This context manager creates a traceback popup, using the popup_exc
    function, if an exception leaves it. Base controls the base exception class
    to check for; other exceptions will be unreported.
    '''
    try:
        yield
    except Base:
        popup_exc(master, title)
        raise


@export
class WaitableToplevel(tkinter.Toplevel, asyncio.Future):
    def __init__(self, *args, loop=None, **kwargs):
        tkinter.Toplevel.__init__(self, *args, **kwargs)
        asyncio.Future.__init__(self, loop=loop)

        # By default, closing the window calls self.destroy. This completes
        # the future and destroyes the Toplevel widget.
        self.protocol('WM_DELETE_WINDOW', self.destroy)

        # When the future is completed- either it is completed by self.destroy,
        # canceled by __exit__, or canceled externally, destroy the Toplevel
        # widget
        self.add_done_callback(super().destroy)

    def destroy(self):
        '''
        Destory the widget, by completing the Future. This triggers the "done"
        callback which actually destroys the widget.
        '''
        if not self.done():
            self.set_result(None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        '''
        When the context is exited, cancel the future. This triggers the done
        coroutine, which actually destroys the widget.
        '''
        self.cancel()


@export
class Respawn(Enum):
    '''
    These are the different respawn behaviors implemented by the `spawn`
    function.
    '''
    CONCURRENT = 0
    CANCEL = 1
    SKIP = 2
    QUEUE = 3


def debug_popup_callback(fut):
    '''
    This callback is attached by `spawn` to the spawned task, to create a popup
    window with the stack trace in the event the task raised an exception. The
    exception is reraised (except a CancelledError) so that a stack trace also
    appears in the CLI.
    '''
    with popup_uncaught():
        try:
            fut.result()
        except asyncio.CancelledError:
            pass

@export
@getloop
def spawn(coro=None, *args, respawn=Respawn.CONCURRENT, debug=None, loop=None):
    '''
    This function creates a callback, suitable for use as a Widget or protocol
    callback, which spawns a coroutine when called. Optionally, pass some
    positional arguments that will be passed to the coroutine when it is
    invoked.

    The callback returned by spawn also takes *args and **kwargs. If given,
    they will be used *after* spawn's *args. This allows spawn callbacks to be
    used as event handlers, or in other contexts outside of tkinter:

        cb = spawn(coro, 1, 2)
        cb(3, 4)  # create a task for coro(1,2,3,4)

    The respawn parameter controls what happens in the event that the callback
    is invoked while a coroutine previously scheduled by this callback is still
    running:

    - Respawn.CONCURRENT: Simply launch a second instance of the coroutine.
      This is the default.
    - Respawn.CANCEL: Cancel the old coroutine and launch a new one.
    - Respawn.SKIP: Do not launch a new coroutine.
    - Respawn.QUEUE: Lauch a new coroutine when the previous one finishes.

    If `debug` is True, a small popup will be created with a stack trace if an
    Exception is raised from the coroutine. asyncio.CancelledError exceptions
    are ignored. If it is not given, it defaults to tkinter_async.DEBUG_POPUPS.

    Examples:
        @asyncio.coroutine
        def my_coroutine():
            ...

        button = tkinter.Button(..., command=spawn(my_coroutine))

    '''

    def spawn_coro(more_args, kwargs):
        '''
        Create and return a new task, running coro with args, more_args, and
        kwargs. If debug mode is enabled, attach a callback which will create a
        popup with the stack trace in the event of an exception.
        '''
        task = loop.create_task(coro(*(args + more_args), **kwargs))
        if debug or (debug is None and DEBUG_POPUPS):
            task.add_done_callback(debug_popup_callback)
        return task

    if respawn is Respawn.CONCURRENT:
        def callback(*args, **kwargs):
            spawn_coro(args, kwargs)

    elif respawn is Respawn.CANCEL:
        running_task = None
        def callback(*args, **kwargs):
            nonlocal running_task
            if running_task is not None:
                running_task.cancel()
            running_task = spawn_coro(args, kwargs)

    elif respawn is Respawn.SKIP:
        running_task = None
        def callback(*args, **kwargs):
            nonlocal running_task
            if running_task is None or running_task.done():
                running_task = spawn_coro(args, kwargs)

    elif respawn is Respawn.QUEUE:
        queue = asyncio.Queue(loop=loop)

        @asyncio.coroutine
        def queue_watcher():
            while True:
                waiter = asyncio.Future(loop=loop)
                args, kwargs = yield from queue.get()
                spawn_coro(args, kwargs).add_done_callback(waiter.set_result)
                yield from waiter

        loop.create_task(queue_watcher())

        def callback(*args, **kwargs):
            queue.put_nowait((args, kwargs))

    else:
        raise TypeError("respawn must be a Respawn Enum", respawn)

    return callback


@asyncio.coroutine
def update_root(root):
    '''
    This coroutine runs a complete iteration of the tkinter event loop for a
    root. It yields in between each individual event, which prevents it from
    blocking the asyncio event loop. It runs until there are no more events in
    the queue, then returns, allowing the caller to do other tasks or sleep
    afterwards. This keeps CPU load low. Generally clients will never need to
    call this function; it should only be used internally by async_mainloop.

    Bear in mind that many single events will actually block indefinitely, such
    as dragging, scrolling, or resizing a window.
    '''
    while root.tk.dooneevent(_DONT_WAIT):
        yield


@getloop
@contextmanager
def timed_section(interval, result=None, *, loop=None):
    '''
    This context manager allows a context to take at least a certain amount of
    time, starting from when the context is *entered*. It yields a future which
    should be yielded from at the end of the context. It allows you to turn
    this:

        end_time = loop.time() + interval
        ...
        yield from asyncio.sleep(end_time - loop.time())

    Into this:

        with timed_section(interval) as delay:
            ...
            yield from delay

    '''

    # This mirrors very closely the implementation of asyncio.sleep. We use the
    # future and call_later explicitly (rather than reusing asyncio.sleep) to
    # ensure the timer is started right when the context is entered
    waiter = asyncio.Future(loop=loop)

    def done():
        if not waiter.cancelled():
            waiter.set_result(result)

    handle = loop.call_later(interval, done)

    try:
        yield waiter
    finally:
        handle.cancel()
        waiter.cancel()


@export
@getloop
@asyncio.coroutine
def async_mainloop(
        root, *,
        interval=.05,
        quit_coro=None,
        quit_respawn=Respawn.SKIP,
        destroy_on_exit=True,
        loop=None):
    '''
    This coroutine replaces root.mainloop() in your tkinter application. Run it
    in the asyncio event loop to run your application inside the loop. This
    allows you to reap the benefits of asyncio- networking, coroutines, etc- in
    your tkinter application. It is designed so that the UI is as responsive as
    possible, without ever blocking the event loop*; the coroutine yields back
    to the asyncio event loop between each individual UI event, giving it the
    opportunity to run other tasks as necessary. By default, it polls the Tk
    event queue at least 20 times per second; This keeps the app responsive
    while keeping CPU load low.

    This coroutine alters the root in two ways. First, it is minimally patched
    such that calling destroy can be detected by the loop. Second, a new
    WM_DELETE_WINDOW exit handler is installed, to ensure the new destroy
    method is called. See the `quit_coro` paramter for how to install your own
    exit handler.

    The `interval` parameter controls how long the loop sleeps between polling
    tkinter's event queue. Note that, when there are events in the queue, they
    are all handled with a yield between each but without a sleep; the interval
    parameter only controls how long the loop sleeps for when idle. This means
    that during event-heavy periods (initial load, new widgets, etc) the
    interface remains as responsive as possible, without actually blocking
    asyncio, but when the interface is idle CPU load remains low.

    The optional `quit_coro` is installed as the exit handler. If given, it is
    run in the event loop when the user tries to quit the application. If it
    returns a False value, the application is destroyed, and async_mainloop
    ends. This is intended to for the application to save state, show a
    confirmation prompt, etc. Note that, because it is a coroutine, the
    application loop will continue while the quit coroutine is running.

    The `quit_respawn` parameter controls the behavior of `quit_coro`. It has
    the same meaning as in `tkinter_async.spawn`: if it is Respawn.SKIP (the
    default), only one instance of the coroutine can be running at a time;
    further attempts to close the window will be ignored until that coroutine
    ends. If it is Respawn.CANCEL, repeated attempts to close the window will
    cancel and relaunch `quit_coro` each time. If it is CONCURRENT, a new
    instance of `quit_coro` will be launched concurrently with each old ones;
    the first one to return False will cause the window to be destroyed.

    If `destroy_on_exit` is True (the default), the root object will be
    destroyed on *any* exit from async_mainloop. While the only normal way to
    end the loop is to destroy the root, this covers cases where an exception
    is raised in the coroutine, such as from a cancelation.

    Example:
        @asyncio.coroutine
        def run_tk_app():
            root = tkinter.Tk()
            yield from async_mainloop(root)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_tk_app())
    '''
    # patch root to support the _destroyed attribute, which is set when the
    # destroy method is called. This is required so that the event loop knows
    # when to terminate, since there is no other way to determine if a window
    # has been destroyed.
    if not hasattr(root, '_destroyed'):
        root._destroyed = False
        original_destroy = root.destroy

        @wraps(original_destroy)
        def destroy():
            if not root._destroyed:
                original_destroy()
                root._destroyed = True

        root.destroy = destroy

    # Install an updated exit handler. If no quit_coro was given, use the
    # updated destroy method; otherwise call quit_coro asynchronously and
    # destroy the window if it returns a false value.
    if quit_coro is None:
        quit_callback = root.destroy

    else:
        @asyncio.coroutine
        def quit_wrapper():
            if not (yield from quit_coro()):
                root.destroy()
        quit_callback = spawn(quit_wrapper, respawn=quit_respawn, loop=loop)

    root.protocol("WM_DELETE_WINDOW", quit_callback)

    # Run the actual mainloop
    try:
        while not root._destroyed:
            with timed_section(interval, loop=loop) as delay:
                yield from update_root(root)
                yield from delay

    finally:
        if not root._destroyed and destroy_on_exit:
            root.destroy()

        # Run one final update cycle, to ensure the destroy() event is handled
        if root._destroyed:
            yield from update_root(root)
