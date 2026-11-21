"""One worker of the post-processing rank reads (postprocess._read_partition_views).

Reads pickled (case_dir, mesh, time_value) tasks from stdin and answers each on
stdout with a pickled ("ok", postprocess._read_rank_packed(...)) or ("error",
traceback); exits at the end of its input. Started with `python -m`, so it never
imports the caller's main script, as multiprocessing's spawn and forkserver do.
"""

import os
import pickle
import sys
import traceback


def main() -> None:
    channel = os.fdopen(os.dup(1), "wb")
    # Whatever Python or VTK prints goes to the caller's log, not the channel.
    os.dup2(2, 1)
    from backend.aircooling.runner import postprocess

    tasks = sys.stdin.buffer
    while True:
        try:
            task = pickle.load(tasks)
        except EOFError:
            return
        try:
            reply = ("ok", postprocess._read_rank_packed(*task))
        except BaseException:  # noqa: BLE001 - reported to the caller, which raises it
            reply = ("error", traceback.format_exc())
        pickle.dump(reply, channel, protocol=pickle.HIGHEST_PROTOCOL)
        channel.flush()


if __name__ == "__main__":
    main()
