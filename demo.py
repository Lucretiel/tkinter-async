import tkinter
from tkinter_async import *
import asyncio
from functools import partial

def make_submitter(root, label, button_text, callback_coro, respawn=Respawn.CONCURRENT):
    frame = tkinter.Frame(root)

    label = tkinter.Label(frame, text=label)
    label.grid(row=0, column=0, sticky='w')

    entry = tkinter.Entry(frame)
    entry.grid(row=0, column=1, sticky='we')

    @asyncio.coroutine
    def submit_callback():
        yield from callback_coro(entry.get())

    button = tkinter.Button(frame, text=button_text,
        command=spawn(submit_callback, respawn=respawn))
    button.grid(row=0, column=2, sticky='we')

    return frame


@asyncio.coroutine
def tk_app():
    root = tkinter.Tk()

    @asyncio.coroutine
    def popup(text):
        '''
        This coroutine creates a popup dialogue with some text. It destroys the
        popup five seconds later.
        '''
        with scoped_window(root) as popup:
            label = tkinter.Label(popup, text=text)
            label.grid()
            yield from asyncio.sleep(5)

    make_submitter(root, "Field 1:", "Go!", popup, Respawn.CONCURRENT).grid(row=0, column=0)
    make_submitter(root, "Field 2:", "Go!", popup, Respawn.SKIP).grid(row=1, column=0)
    make_submitter(root, "Field 3:", "Go!", popup, Respawn.CANCEL).grid(row=2, column=0)

    @asyncio.coroutine
    def exception_coro():
        raise RuntimeError("This coroutine raised an exception")

    tkinter.Button(root, text="Exception!", command=spawn(exception_coro)).grid(row=3)

    @asyncio.coroutine
    def confirm_quit():
        with scoped_window(root) as popup:
            label = tkinter.Label(popup, text="Are you sure you want to quit?")
            label.grid(columnspan=2)

            result = asyncio.Future()

            do_quit = partial(result.set_result, False)
            no_quit = partial(result.set_result, True)

            yes = tkinter.Button(popup, text="Yes", command=do_quit)
            yes.grid(row=1)

            no = tkinter.Button(popup, text="No", command=no_quit)
            no.grid(row=1, column=1)

            popup.protocol("WM_DELETE_WINDOW", no_quit)

            return (yield from result)

    yield from async_mainloop(root, quit_coro=confirm_quit)

@asyncio.coroutine
def run():
    yield from tk_app()

if __name__== "__main__":
    asyncio.get_event_loop().run_until_complete(run())
