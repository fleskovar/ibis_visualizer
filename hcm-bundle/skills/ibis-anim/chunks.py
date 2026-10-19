"""List the ibis models of a dbt-ibis trace, or render the steps of one model in videos of a few steps each.

usage: python chunks.py MODEL_FILE                         each ibis model of the trace and its steps; no video
       python chunks.py MODEL_FILE --size N                the steps of the model, in videos of at most N steps
       python chunks.py MODEL_FILE --size N --model NAME   the same, for the model NAME of the trace

The videos are media/<model>_part1.mp4, media/<model>_part2.mp4, ... Each part has the facts of the whole trace.
Set IBIS_ANIM_QUALITY=low for a quick check. Run it with the Python of the project, which has ibis-anim.
"""

import argparse

import ibis_anim

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("model_file", help="the file of the dbt-ibis model (.py or .ibis)")
parser.add_argument("--size", type=int, help="the number of steps in each video; without it, list the models and render nothing")
parser.add_argument("--model", help="the model to render, a model of the trace (default: the model of MODEL_FILE)")
args = parser.parse_args()

name, by_model, facts = ibis_anim.model_steps(args.model_file)
if args.size is None:
    for model, steps in by_model.items():  # in the order that the models ran: the sources first
        print(f"{model}: {len(steps)} step{'s' * (len(steps) != 1)}: {', '.join(s.op for s in steps)}")
else:
    model = args.model or name
    if model not in by_model:
        parser.exit(1, f"chunks.py: no steps of {model}; the models with steps are {', '.join(by_model)}\n")
    steps = by_model[model]
    for k in range(0, len(steps), args.size):
        print(f"chunks.py: video at {ibis_anim.render(steps[k:k + args.size], f'{model}_part{k // args.size + 1}', facts=facts)}")
