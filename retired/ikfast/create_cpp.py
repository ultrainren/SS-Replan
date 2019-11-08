#!/usr/bin/env python2

from __future__ import print_function

import os

from openravepy import Environment, ikfast
from openravepy.misc import InitOpenRAVELogging

# IKFast
# https://github.mit.edu/Learning-and-Intelligent-Systems/ltamp_pr2/tree/master/control_tools/ik
# https://github.mit.edu/Learning-and-Intelligent-Systems/ltamp_pr2/blob/master/control_tools/ik/ik_tools/pr2_with_sensor_ik/ik_generator.py
# http://openrave.org/docs/0.8.2/openravepy/ikfast/
# http://docs.ros.org/kinetic/api/moveit_tutorials/html/doc/ikfast/ikfast_tutorial.html
# http://docs.ros.org/kinetic/api/framefab_irb6600_support/html/doc/ikfast_tutorial.html
# https://github.com/yijiangh/choreo/blob/bc777069b8eb7283c74af26e5461532aec3d9e8a/framefab_robot/abb/framefab_irb6600/framefab_irb6600_support/doc/ikfast_tutorial.rst
# http://wiki.ros.org/collada_urdf
# https://github.mit.edu/Learning-and-Intelligent-Systems/ltamp_pr2/commit/37c6a3054e392197cf5ecfa88e5a9afe88f3f674#diff-6df7cef0f75bf084742e858c5b8bad69
# https://github.com/ros-planning/moveit_ikfast/blob/kinetic-devel/scripts/round_collada_numbers.py

# pip install sympy==0.7.1 --upgrade --user
from utils import DAE_PATH, BASE_LINK, TOOL_LINK, FREE_JOINT, CPP_PATH

InitOpenRAVELogging()
env = Environment()
# If needed, use the following command to generate a .dae file from a .urdf file.
# rosrun collada_urdf urdf_to_collada <input-urdf> <output.dae>

kinbody = env.ReadRobotURI(DAE_PATH)
env.Add(kinbody)
solver = ikfast.IKFastSolver(kinbody=kinbody)

base_link = kinbody.GetLink(BASE_LINK).GetIndex()
ee_link = kinbody.GetLink(TOOL_LINK).GetIndex()
free_joints = [
    #kinbody.GetJoint('l_upper_arm_roll_joint').GetDOFIndex(), # Third link
    kinbody.GetJoint(FREE_JOINT).GetDOFIndex(),
]

chaintree = solver.generateIkSolver(baselink=base_link,
									eelink=ee_link,
									freeindices=free_joints,
									solvefn=ikfast.IKFastSolver.solveFullIK_6D)

code = solver.writeIkSolver(chaintree)
with open(CPP_PATH, 'w') as f:
    f.write(code)
print('Wrote', CPP_PATH)

# scp create_cpp.py demo@128.30.47.147:/home/demo/