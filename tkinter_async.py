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


from contextlib import contextmanager
import asyncio
import enum
from traceback import format_exc
from functools import wraps
import tkinter


_DONT_WAIT = tkinter._tkinter.DONT_WAIT


__all__ = []


def export(thing):
    '''
    Decorator to add things with a __name__ to __all__
    '''
    __all__.append(thing.__name__)
    return thing


# if tkinter.DEBUG is False, no debug popups will be created from `spawn`
# coroutines (by default)
DEBUG = True


def getloop(func):
    '''
    This decorator modifies a function taking a `loop` kwarg to automatically
    look up the loop with asyncio.get_event_loop() when called, if it's None.
    '''
    @wraps(func)
    def wrapper(*args, loop=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        return func(*args, loop=loop, **kwargs)
    return wrapper


@export
@contextmanager
def scoped_window(*args, ignore_destroyed=True, **kwargs):
    '''
    This context manager creates a tkinter.Toplevel popup and destroys it at
    the end of the context. In plain Tk this really isn't possible, because it
    exclusively uses callbacks to manage events. However, with coroutines, it's
    possible to simply contrain the lifespan of an object to that of the
    coroutine, significantly simplifying the design of the application.

    If `ignore_destroyed` is True (the default), exceptions raised during the
    destroy are ignored. This is because, in some cases, destroying an object
    more than once results in a TclException being raised.

    Example:

        @asyncio.coroutine
        def temp_popup(master, text, interval):
            # Create a temporary popup.
            with scoped_window(master) as popup:
                label = tkinter.Frame(window, text=text)
                label.grid()
                yield from asyncio.sleep(interval)
                # window is destroyed at the end of the context, and the event
                # loop can proceed during the asyncio.sleep.
    '''
    popup = tkinter.Toplevel(*args, **kwargs)
    try:
        yield popup
    finally:
        try:
            popup.destroy()
        except tkinter.TclException:
            if not ignore_destroyed:
                raise


@export
class Respawn(enum.Enum):
    '''
    These are the different respawn behaviors implemented by the `spawn`
    function.
    '''
    CONCURRENT = 0
    CANCEL = 1
    SKIP = 2


@export
@getloop
def spawn(coro=None, *args, respawn=Respawn.CONCURRENT, debug=None, loop=None):
    '''
    This function creates a callback, suitable for use as a Widget or protocol
    callback, which spawns a coroutine asynchronously. Optionally, pass some
    arguments that will be passed to the coroutine when it is invoked; for more
    complex cases (like kwargs), use a locally defined coroutine, lambda, or
    functools.partial to capture the data you need.

    The respawn parameter controls what happens in the event that the callback
    is invoked while a coroutine previously scheduled by this callback is still
    running:

    - Respawn.CONCURRENT: Simply schedule a second instance of the coroutine.
      This is the default.
    - Respawn.CANCEL: Cancel the old coroutine and start a new one.
    - Respanw.SKIP: Do not schedule a new coroutine.

    If `debug` is True, a small popup will be created with a stack trace if an
    Exception is raised from the coroutine. asyncio.CancelledError exceptions
    are ignored. If it is not given, it defaults to tkinter_async.DEBUG.

    This function can also be used as a decorator. In that case, it converts
    the coroutine into the callback. Keep in mind that a single callback
    maintains only a single running task for SKIP or CANCEL; if you want
    separate callbacks with the same coroutine, with independant SKIP or
    CANCEL, call spawn each time.

    Examples:
        @asyncio.coroutine
        def my_coroutine():
            ...

        button = tkinter.Button(..., command=spawn(my_coroutine))

        @spawn
        @asyncio.coroutine
        def my_coroutine2():
            ...

        button2 = tkinter.Button(command=my_coroutine2)

        @spawn(respawn=Respawn.SKIP)
        @asyncio.coroutine
        def my_coroutine3():
            ...

        button3 = tkinter.Button(command=my_coroutine3)

    '''
    # Decorator-with-arguments variant
    if coro is None:
        return lambda coro: spawn(
            coro, *args, respawn=respawn, debug=debug, loop=loop)

    # Exception traceback popup window
    if debug or (debug is None and DEBUG):
        @asyncio.coroutine
        def coro_wrapper():
            try:
                yield from coro(*args)
            except asyncio.CancelledError:
                raise
            except Exception:
                window = tkinter.Toplevel()
                window.title("Exception raised from spawned coroutine")
                label = tkinter.Label(
                    window,
                    text=format_exc(),
                    justify=tkinter.LEFT)
                label.grid()
                raise

    else:
        @asyncio.coroutine
        def coro_wrapper():
            yield from coro(*args)

    def spawn_coro():
        return asyncio.async(coro_wrapper(), loop=loop)

    task = None
    if respawn is Respawn.CONCURRENT:
        def callback():
            spawn_coro()

    elif respawn is Respawn.CANCEL:
        def callback():
            nonlocal task
            if task is not None:
                task.cancel()
            task = spawn_coro()

    elif respawn is Respawn.SKIP:
        def callback():
            nonlocal task
            if task is None or task.done():
                task = spawn_coro()
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
    '''
    while root.tk.dooneevent(_DONT_WAIT):
        yield


@getloop
@contextmanager
def timed_section(interval, result=None, *, loop=None):
    '''
    This context manager forces a context to take at least a certain amount of
    time, starting from when the context is entered. It yields a future which
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

    # This mirrors very closely the implementation of asyncio.sleep.
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
    in the asyncio event loop to run your application alongside the loop. This
    allows you to reap the benefits of asyncio- networking, coroutines, etc- in
    your tkinter application. It is designed so that the UI is as responsive as
    possible, without ever blocking the event loop; the coroutine yields back
    to the asyncio event loop between each individual UI event, giving it the
    opportunity to run other tasks as necessary. By default, it polls the Tk
    event queue at least 20 times per second; This keeps the app responsive
    while keeping CPU load low.

    This coroutine alters the root in two ways. First, if root is *not* an
    instance of AsyncTk, it is minimally patched such that calling destroy can
    be detected by the loop. Second, a new WM_DELETE_WINDOW exit handler is
    installed, to ensure the new destroy method (or AsyncTk's destroy method)
    is called. See the `quit_coro` paramter for how to install your own exit
    handler.

    The `interval` parameter controls how long the loop sleeps between polling
    tkinter's event queue. Note that, when there are events in the queue, they
    are all handled with a yield between each but without a sleep; the interval
    parameter only controls how long the loop sleeps for when idle. This means
    that during event-heavy periods (initial load, new widgets, and resize) the
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
