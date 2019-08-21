#!/usr/bin/env python2

from __future__ import print_function

import sys
import argparse
import os
import numpy as np

sys.path.extend(os.path.abspath(os.path.join(os.getcwd(), d))
                for d in ['pddlstream', 'ss-pybullet'])

from pybullet_tools.utils import wait_for_user, LockRenderer, \
    get_random_seed, get_numpy_seed, VideoSaver
from src.command import create_state
from src.visualization import add_markers
from src.observe import observe_pybullet
#from src.debug import test_observation
from src.planner import VIDEO_TEMPLATE, iterate_commands
from src.world import World
from src.task import TASKS
from src.policy import run_policy
#from src.debug import dump_link_cross_sections, test_rays

def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-anytime', action='store_true',
                        help='Runs in an anytime mode')
    parser.add_argument('-cfree', action='store_true',
                        help='When enabled, disables collision checking (for debugging).')
    #parser.add_argument('-defer', action='store_true',
    #                    help='When enabled, defers evaluation of motion planning streams.')
    parser.add_argument('-deterministic', action='store_true',
                        help='Treats actions as fully deterministic')
    parser.add_argument('-observable', action='store_true',
                        help='Treats the state as fully observable')
    parser.add_argument('-max_time', default=3*60, type=int,
                        help='The max computation time')
    parser.add_argument('-record', action='store_true',
                        help='When enabled, records and saves a video at {}'.format(
                            VIDEO_TEMPLATE.format('<problem>')))
    #parser.add_argument('-seed', default=None,
    #                    help='The random seed to use.')
    parser.add_argument('-teleport', action='store_true',
                        help='When enabled, motion planning is skipped')
    parser.add_argument('-unit', action='store_true',
                        help='When enabled, uses unit costs')
    parser.add_argument('-visualize', action='store_true',
                        help='When enabled, visualizes the planning world '
                             'rather than the simulated world (for debugging).')
    return parser
    # TODO: get rid of funky orientations by dropping them from some height

################################################################################

def main():
    task_names = [fn.__name__ for fn in TASKS]
    print('Tasks:', task_names)
    parser = create_parser()
    parser.add_argument('-problem', default=task_names[0], choices=task_names,
                        help='The name of the problem to solve.')
    args = parser.parse_args()
    #if args.seed is not None:
    #    set_seed(args.seed)
    #set_random_seed(None) # Doesn't ensure deterministic
    #set_numpy_seed(None)
    print('Random seed:', get_random_seed())
    print('Numpy seed:', get_numpy_seed())

    np.set_printoptions(precision=3, suppress=True)
    world = World(use_gui=True)
    task_fn_from_name = {fn.__name__: fn for fn in TASKS}
    task_fn = task_fn_from_name[args.problem]

    task = task_fn(world)
    world._update_initial()
    if not args.record:
        with LockRenderer():
            add_markers(task, inverse_place=False)
    #wait_for_user()
    # TODO: FD instantiation is slightly slow to a deepcopy
    # 4650801/25658    2.695    0.000    8.169    0.000 /home/caelan/Programs/srlstream/pddlstream/pddlstream/algorithms/skeleton.py:114(do_evaluate_helper)
    #test_observation(world, entity_name='big_red_block0')
    #return

    # TODO: mechanism that pickles the state of the world
    real_state = create_state(world)
    video = None
    if args.record:
        wait_for_user('Start?')
        video = VideoSaver(VIDEO_TEMPLATE.format(args.problem))

    def observation_fn(belief):
        return observe_pybullet(world)

    def transition_fn(belief, commands):
        # TODO: fixed-base planning and execution
        # Multiple rays for detecting
        # restore real_state just in case?
        # wait_for_user()
        # simulate_plan(real_state, commands, args)
        return iterate_commands(real_state, commands)

    run_policy(task, args, observation_fn, transition_fn)

    if video:
        video.restore()
    world.destroy()
    # TODO: make the sink extrude from the mesh

if __name__ == '__main__':
    main()
