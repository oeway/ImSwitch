from __future__ import print_function
from random import random
import sys
import json
sys.path.append('..')
from dorna2 import dorna


def random_joint():
    j0 = -90 + random() * 180
    j1 = -45 + random() * 135
    j2 = max(-90, -2.7*j1 - 60) + random() * (90 - max(-90, -2.7*j1 - 60))
    j3 = -90 + random() * 180
    j4 = 0
    return j0, j1, j2, j3, j4


def main(robot):
    # go home
    arg = {"rel": 0, "id": robot.rand_id(), "j0": 0, "j1": 0, "j2": 0, "j3": 0, "j4": 0, "vel": 50, "accel": 300, "jerk": 1000}
    print("going to start ->")
    robot.jmove(**arg)

    # random points
    i = 0
    while i < 10:
        j0, j1, j2, j3, j4 = random_joint()

        arg = {"rel": 0, "id": i+1, "j0": j0, "j1": j1, "j2": j2, "j3": j3, "j4": j4}
        print("command", i, "   arg: ", arg)

        robot.jmove(**arg)
        i += 1

if __name__ == '__main__':
    config_path = "config.json"
    
    # arguments
    robot = dorna.Dorna()
    print("connecting")
    IP = "192.168.178.41"
    PORT = 443
    if not robot.connect(IP, PORT):
        print("not connected")
    else:
        print("connected")
        robot.set_pwm(0, 1) # enable pwm channel 0
        robot.set_freq(index=0, freq=50)
        robot.set_duty(index=0, duty=8)
        
        robot.set_duty(index=0, duty=100)
        
        main(robot)
    robot.close()