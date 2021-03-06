#!/usr/bin/env python2

from __future__ import print_function

# https://github.mit.edu/Learning-and-Intelligent-Systems/ltamp_pr2/blob/d1e6024c5c13df7edeab3a271b745e656a794b02/learn_tools/collect_simulation.py
# https://github.mit.edu/caelan/pddlstream-experiments/blob/master/run_experiment.py

import argparse
import numpy as np
import time
import datetime
import math
import numpy
import random
import os
import sys
import traceback
import resource
import copy
import psutil

PACKAGES = ['pddlstream', 'ss-pybullet']

def add_packages(packages):
    sys.path.extend(os.path.abspath(os.path.join(os.getcwd(), d)) for d in packages)

add_packages(PACKAGES)

np.set_printoptions(precision=3, threshold=3, edgeitems=1, suppress=True) #, linewidth=1000)

import pddlstream.language.statistics
pddlstream.language.statistics.LOAD_STATISTICS = False
pddlstream.language.statistics.SAVE_STATISTICS = False

from pybullet_tools.utils import has_gui, elapsed_time, user_input, ensure_dir, \
    wrap_numpy_seed, timeout, write_json, SEPARATOR, WorldSaver, \
    get_random_seed, get_numpy_seed, set_random_seed, set_numpy_seed, wait_for_user, get_date, is_darwin, INF
from pddlstream.utils import str_from_object, safe_rm_dir, Verbose, KILOBYTES_PER_GIGABYTE, BYTES_PER_KILOBYTE
from pddlstream.algorithms.algorithm import reset_globals

from src.command import create_state, iterate_commands
from src.observe import observe_pybullet
from src.world import World
from src.policy import run_policy
from src.task import cook_block, TASKS_FNS
from run_pybullet import create_parser

from multiprocessing import Pool, TimeoutError, cpu_count

EXPERIMENTS_DIRECTORY = 'experiments/'
TEMP_DIRECTORY = 'temp_parallel/'
MAX_TIME = 10*60
TIME_BUFFER = 60
SERIAL = is_darwin()
VERBOSE = SERIAL
SERIALIZE_TASK = True

MEAN_TIME_PER_TRIAL = 300 # trial / sec
HOURS_TO_SECS = 60 * 60

N_TRIALS = 1 # 1
MAX_MEMORY = 3.5*KILOBYTES_PER_GIGABYTE
SPARE_CORES = 4

POLICIES = [
    {'constrain': False, 'defer': False},
    {'constrain': True, 'defer': False},
    {'constrain': False, 'defer': True},  # Move actions grow immensely
    {'constrain': True, 'defer': True},
    # TODO: serialize
]
# 8Gb memory limit
# https://ipc2018-classical.bitbucket.io/

# Tasks
# 1) Inspect drawers
# 2) Swap drawers (uniform prior)
# 3) Object behind one of two objects
# 4) Cook meal
# 6) Object on drawer that needs to be moved
# 7) Crowded surface
# 8) Scaling to longer tasks (no point if serializing)
# 9) Packing into drawer
# 10) Fixed base manipulation
# 11) Regrasp using the cabinet
# 12) Irrelevant distractors that aren't picked up

TASK_NAMES = [
    'inspect_drawer',
    'sugar_drawer',
    'swap_drawers',
    'detect_block',

    #'cook_meal',
    #'regrasp_block',
    #'hold_block',
    #'cook_block',
    #'stow_block',
]

# TODO: CPU usage at 300% due to TracIK or the visualizer?
# TODO: could check collisions only with real (non-observed) values

ERROR_OUTCOME = {
    'error': True,
    'achieved_goal': False,
    'total_time': INF,
    'plan_time': INF,
    'num_iterations': 0,
    'num_constrained': 0,
    'num_unconstrained': 0,
    'num_successes': 0,
    'num_actions': INF,
    'num_commands': INF,
    'total_cost': INF,
}

# TODO: doesn't work on flakey

################################################################################

def map_parallel(fn, inputs, num_cores=None): #, timeout=None):
    # Processes rather than threads (shared memory)
    # TODO: with statement on Pool
    if SERIAL:
        for outputs in map(fn, inputs):
            yield outputs
        return
    pool = Pool(processes=num_cores) #, initializer=mute)
    generator = pool.imap_unordered(fn, inputs) #, chunksize=1)
    # pool_result = pool.map_async(worker, args)
    #return generator
    while True:
        # TODO: need to actually retrieve the info about which thread failed
        try:
            yield generator.next() # timeout=timeout)
        except StopIteration:
            break
        #except MemoryError: # as e:
        #    traceback.print_exc()
        #    continue
        #except TimeoutError: # as e:
        #    traceback.print_exc() # Kills all jobs
        #    continue
    if pool is not None:
        pool.close()
        pool.terminate()
        pool.join()
    #import psutil
    #if parallel:
    #    process = psutil.Process(os.getpid())
    #    print(process)
    #    print(process.get_memory_info())

################################################################################

def name_from_policy(policy):
    return '_'.join('{}={:d}'.format(key, value) for key, value in sorted(policy.items()))

def set_memory_limits():
    # ulimit -a
    # soft, hard = resource.getrlimit(name) # resource.RLIM_INFINITY
    # resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    process = psutil.Process(os.getpid())
    soft_memory = int(BYTES_PER_KILOBYTE*MAX_MEMORY)
    hard_memory = soft_memory
    process.rlimit(psutil.RLIMIT_AS, (soft_memory, hard_memory))
    # TODO: AttributeError: 'Process' object has no attribute 'rlimit'
    #soft_time = MAX_TIME + 2*60 # I think this kills the wrong things
    #hard_time = soft_time
    #process.rlimit(psutil.RLIMIT_CPU, (soft_time, hard_time))

################################################################################

def run_experiment(experiment):
    problem = experiment['problem']
    task_name = problem['task'].name if SERIALIZE_TASK else problem['task']
    trial = problem['trial']
    policy = experiment['policy']
    set_memory_limits()

    if not VERBOSE:
       sys.stdout = open(os.devnull, 'w')
       stdout = sys.stdout
    if not SERIAL:
        current_wd = os.getcwd()
        # trial_wd = os.path.join(current_wd, TEMP_DIRECTORY, '{}/'.format(os.getpid()))
        trial_wd = os.path.join(current_wd, TEMP_DIRECTORY, 't={}_n={}_{}/'.format(
            task_name, trial, name_from_policy(policy)))
        safe_rm_dir(trial_wd)
        ensure_dir(trial_wd)
        os.chdir(trial_wd)

    parser = create_parser()
    args = parser.parse_args()

    task_fn_from_name = {fn.__name__: fn for fn in TASKS_FNS}
    task_fn = task_fn_from_name[task_name]
    world = World(use_gui=SERIAL)
    if SERIALIZE_TASK:
        task_fn(world, fixed=args.fixed)
        task = problem['task']
        world.task = task
        task.world = world
    else:
        # TODO: assumes task_fn is deterministic wrt task
        task_fn(world, fixed=args.fixed)
    problem['saver'].restore()
    world._update_initial()
    problem['task'] = task_name # for serialization
    del problem['saver']

    random.seed(hash((0, task_name, trial, time.time())))
    numpy.random.seed(hash((1, task_name, trial, time.time())) % (2**32))
    #seed1, seed2 = problem['seeds'] # No point unless you maintain the same random state per generator
    #set_random_seed(seed1)
    #set_random_seed(seed2)
    #random.setstate(state1)
    #numpy.random.set_state(state2)
    reset_globals()
    real_state = create_state(world)
    #start_time = time.time()
    #if has_gui():
    #    wait_for_user()

    observation_fn = lambda belief: observe_pybullet(world)
    transition_fn = lambda belief, commands: iterate_commands(real_state, commands, time_step=0)
    outcome = dict(ERROR_OUTCOME)
    try:
        with timeout(MAX_TIME + TIME_BUFFER):
            outcome = run_policy(task, args, observation_fn, transition_fn, max_time=MAX_TIME, **policy)
            outcome['error'] = False
    except KeyboardInterrupt:
        raise KeyboardInterrupt()
    except:
        traceback.print_exc()
        #outcome = {'error': True}

    world.destroy()
    if not SERIAL:
        os.chdir(current_wd)
        safe_rm_dir(trial_wd)
    if not VERBOSE:
        sys.stdout.close()
        sys.stdout = stdout

    result = {
        'experiment': experiment,
        'outcome': outcome,
    }
    return result

################################################################################

def create_problems(args):
    task_fn_from_name = {fn.__name__: fn for fn in TASKS_FNS}
    problems = []
    for num in range(N_TRIALS):
        for task_name in TASK_NAMES:
            print('Trial: {} / {} | Task: {}'.format(num, N_TRIALS, task_name))
            random.seed(hash((0, task_name, num, time.time())))
            numpy.random.seed(wrap_numpy_seed(hash((1, task_name, num, time.time()))))
            world = World(use_gui=False) # SERIAL
            task_fn = task_fn_from_name[task_name]
            task = task_fn(world, fixed=args.fixed)
            task.world = None
            if not SERIALIZE_TASK:
                task = task_name
            saver = WorldSaver()
            problems.append({
                'task': task,
                'trial': num,
                'saver': saver,
                #'seeds': [get_random_seed(), get_numpy_seed()],
                #'seeds': [random.getstate(), numpy.random.get_state()],
            })
            #print(world.body_from_name) # TODO: does not remain the same
            #wait_for_user()
            #world.reset()
            #if has_gui():
            #    wait_for_user()
            world.destroy()
    return problems

################################################################################

def main():
    parser = create_parser()
    args = parser.parse_args()
    print(args)

    # https://stackoverflow.com/questions/15314189/python-multiprocessing-pool-hangs-at-join
    # https://stackoverflow.com/questions/39884898/large-amount-of-multiprocessing-process-causing-deadlock
    # TODO: alternatively don't destroy the world
    num_cores = max(1, cpu_count() - SPARE_CORES)
    json_path = os.path.abspath(os.path.join(EXPERIMENTS_DIRECTORY, '{}.json'.format(get_date())))

    #memory_per_core = float(MAX_RAM) / num_cores # gigabytes
    #set_soft_limit(resource.RLIMIT_AS, int(BYTES_PER_GIGABYTE * memory_per_core)) # bytes
    #set_soft_limit(resource.RLIMIT_CPU, 2*MAX_TIME) # seconds
    # RLIMIT_MEMLOCK, RLIMIT_STACK, RLIMIT_DATA

    print('Results:', json_path)
    print('Num Cores:', num_cores)
    #print('Memory per Core: {:.2f}'.format(memory_per_core))
    print('Tasks: {} | {}'.format(len(TASK_NAMES), TASK_NAMES))
    print('Policies: {} | {}'.format(len(POLICIES), POLICIES))
    print('Num Trials:', N_TRIALS)
    num_experiments = len(TASK_NAMES) * len(POLICIES) * N_TRIALS
    print('Num Experiments:', num_experiments)
    max_parallel = math.ceil(float(num_experiments) / num_cores)
    print('Estimated duration: {:.2f} hours'.format(MEAN_TIME_PER_TRIAL * max_parallel / HOURS_TO_SECS))
    user_input('Begin?')
    print(SEPARATOR)

    print('Creating problems')
    start_time = time.time()
    problems = create_problems(args)
    experiments = [{'problem': copy.deepcopy(problem), 'policy': policy} #, 'args': args}
                   for problem in problems for policy in POLICIES]
    print('Created {} problems and {} experiments in {:.3f} seconds'.format(
        len(problems), len(experiments), elapsed_time(start_time)))
    print(SEPARATOR)

    ensure_dir(EXPERIMENTS_DIRECTORY)
    safe_rm_dir(TEMP_DIRECTORY)
    ensure_dir(TEMP_DIRECTORY)
    start_time = time.time()
    results = []
    try:
        for result in map_parallel(run_experiment, experiments, num_cores=num_cores):
            results.append(result)
            print('{}\nExperiments: {} / {} | Time: {:.3f}'.format(
                SEPARATOR, len(results), len(experiments), elapsed_time(start_time)))
            print('Experiment:', str_from_object(result['experiment']))
            print('Outcome:', str_from_object(result['outcome']))
            write_json(json_path, results)
    #except BaseException as e:
    #    traceback.print_exc() # e
    finally:
        if results:
            write_json(json_path, results)
        print(SEPARATOR)
        print('Saved:', json_path)
        print('Results:', len(results))
        print('Duration / experiment: {:.3f}'.format(num_cores*elapsed_time(start_time) / len(experiments)))
        print('Duration: {:.2f} hours'.format(elapsed_time(start_time) / HOURS_TO_SECS))
        safe_rm_dir(TEMP_DIRECTORY)
        # TODO: dump results automatically?
    return results

#  ./run_experiment.py 2>&1 | tee log.txt

if __name__ == '__main__':
    main()

"""
_memory: 1571984.0, plan_time: 210.628690958, total_cost: 2075, total_time: 211.089640856}
WARNING: overflow on h^add! Costs clamped to 100000000
Traceback (most recent call last):
  File "./run_experiment.py", line 202, in run_experiment
    max_time=MAX_TIME, **policy)
  File "/home/caelan/Programs/srlstream/src/policy.py", line 113, in run_policy
    max_cost=plan_cost, replan_actions=defer_actions)
  File "/home/caelan/Programs/srlstream/src/policy.py", line 34, in random_restart
    plan, plan_cost, certificate = solve_pddlstream(belief, problem, args, max_time=remaining_time, **kwargs)
  File "/home/caelan/Programs/srlstream/src/planner.py", line 167, in solve_pddlstream
    search_sample_ratio=search_sample_ratio)
  File "/home/caelan/Programs/srlstream/pddlstream/pddlstream/algorithms/focused.py", line 134, in solve_focused
  File "/home/caelan/Programs/srlstream/pddlstream/pddlstream/algorithms/reorder.py", line 167, in reorder_stream_plan
    ordering = dynamic_programming(nodes, valid_combine, stats_fn, **kwargs)
  File "/home/caelan/Programs/srlstream/pddlstream/pddlstream/algorithms/reorder.py", line 127, in dynamic_programming
    new_subset = frozenset([v]) | subset
MemoryError
"""
