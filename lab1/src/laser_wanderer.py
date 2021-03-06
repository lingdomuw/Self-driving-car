#!/usr/bin/env python

import rospy
import numpy as np
import math
import sys

import utils

from sensor_msgs.msg import LaserScan
from ackermann_msgs.msg import AckermannDriveStamped
from geometry_msgs.msg import PoseStamped, PoseArray, Pose

SCAN_TOPIC = '/car/scan'                                            # The topic to subscribe to for laser scans
CMD_TOPIC = '/car/mux/ackermann_cmd_mux/input/navigation'
POSE_TOPIC = '/car/car_pose'                              
                                                                # NOTE THAT THIS IS ONLY NECESSARY FOR VIZUALIZATION
VIZ_TOPIC = '/laser_wanderer/rollouts'                          # The topic to publish to for vizualizing
                                                                # the computed rollouts. Publish a PoseArray.
MAX_PENALTY = 10000                                             # The penalty to apply when a configuration in a rollout
                                                                # goes beyond the corresponding laser scan

'''
Wanders around using minimum (steering angle) control effort while avoiding crashing
based off of laser scans. 
'''
class LaserWanderer:

    '''
    Initializes the LaserWanderer
    rollouts:       An NxTx3 numpy array that contains N rolled out trajectories, each
                    containing T poses. For each trajectory, the t-th element represents
                    the [x,y,theta] pose of the car at time t+1
    deltas:         An N dimensional array containing the possible steering angles. The n-th
                    element of this array is the steering angle that would result in the 
                    n-th trajectory in rollouts
    speed:          The speed at which the car should travel
    compute_time:   The amount of time (in seconds) we can spend computing the cost
    laser_offset:   How much to shorten the laser measurements
    '''
    def __init__(self, rollouts, deltas, speed, compute_time, laser_offset):
        # Store the params for later
        self.rollouts = rollouts
        self.deltas = deltas
        self.speed = speed
        self.compute_time = compute_time
        self.laser_offset = laser_offset

        # YOUR CODE HERE
        # NOTE THAT THIS VIZUALIZATION WILL ONLY WORK IN SIMULATION.
        self.cmd_pub = rospy.Publisher(CMD_TOPIC, AckermannDriveStamped, queue_size=1)          # Create a publisher for sending controls
        self.laser_sub = rospy.Subscriber(SCAN_TOPIC, LaserScan, self.wander_cb, queue_size=1)  # Create a subscriber to laser scans that uses the self.wander_cb callback
        self.viz_pub = rospy.Publisher(VIZ_TOPIC, PoseArray, queue_size=1)                      # Create a publisher for vizualizing trajectories. Will publish PoseArrays  
        self.viz_sub = rospy.Subscriber(POSE_TOPIC, PoseStamped, self.viz_sub_cb, queue_size=1) # Create a subscriber to the current position of the car
    
    '''
    Vizualize the rollouts. Transforms the rollouts to be in the frame of the world.
    Only display the last pose of each rollout to prevent lagginess
    msg: A PoseStamped representing the current pose of the car
    '''
    def viz_sub_cb(self, msg):
        # Create the PoseArray to publish. Will contain N poses, where the n-th pose
        # represents the last pose in the n-th trajectory
        pa = PoseArray()
        pa.header.frame_id = '/car/base_link'
        pa.header.stamp = rospy.Time.now()

        # Transform the last pose of each trajectory to be w.r.t the world and insert into
        # the pose array
        
        for node in self.rollouts:
            pose = Pose()
            pose.position.x = node[-1,0]
            pose.position.y = node[-1,1]
            m =node[-1,2]
            pose.orientation = utils.angle_to_quaternion(m)
            pa.poses.append(pose)
        self.viz_pub.publish(pa)
    
    '''
    Compute the cost of one step in the trajectory. It should penalize the magnitude
    of the steering angle. It should also heavily penalize crashing into an object
    (as determined by the laser scans)
    delta: The steering angle that corresponds to this trajectory
    rollout_pose: The pose in the trajectory 
    laser_msg: The most recent laser scan
    '''  
    def compute_cost(self, delta, rollout_pose, laser_msg):

        # Initialize the cost to be the magnitude of delta
        # Consider the line that goes from the robot to the rollout pose
        # Compute the angle of this line with respect to the robot's x axis
        # Find the laser ray that corresponds to this angle
        # Add MAX_PENALTY to the cost if the distance from the robot to the rollout_pose 
        # is greater than the laser ray measurement - np.abs(self.laser_offset)
        # Return the resulting cost
        # Things to think about:
        #   What if the angle of the pose is less (or greater) than the angle of the
        #   minimum (or maximum) laser scan angle
        #   What if the corresponding laser measurement is NAN?
        # NOTE THAT NO COORDINATE TRANSFORMS ARE NECESSARY INSIDE OF THIS FUNCTION

        cost = np.abs(delta)

        angle = math.atan2(rollout_pose[1], rollout_pose[0])

        laser_distance0 = laser_msg.ranges[int(round((angle-laser_msg.angle_min)/laser_msg.angle_increment))]
        laser_distance1 = laser_msg.ranges[int(round((angle-laser_msg.angle_min)/laser_msg.angle_increment))+1]
        laser_distance2 = laser_msg.ranges[int(round((angle-laser_msg.angle_min)/laser_msg.angle_increment))-1]
        
        pose_distance = math.pow(rollout_pose[0],2) + math.pow(rollout_pose[1],2)

        if pose_distance > (laser_distance0 - np.abs(self.laser_offset)):
            cost = cost + MAX_PENALTY
        if pose_distance > (laser_distance1 - np.abs(self.laser_offset)):
            cost = cost + MAX_PENALTY
        if pose_distance > (laser_distance2 - np.abs(self.laser_offset)):
            cost = cost + MAX_PENALTY

        cost = cost / 3

        return cost
    
    '''
    Controls the steering angle in response to the received laser scan. Uses approximately
    self.compute_time amount of time to compute the control
    msg: A LaserScan
    '''
    def wander_cb(self, msg):
        start = rospy.Time.now().to_sec()   # Get the time at which this function started

        # A N dimensional matrix that should be populated with the costs of each
        # trajectory up to time t <= T
        delta_costs = np.zeros(self.deltas.shape[0], dtype=np.float) 
        traj_depth = 0
        
        # Evaluate the cost of each trajectory. Each iteration of the loop should calculate
        # the cost of each trajectory at time t = traj_depth and add those costs to delta_costs
        # as appropriate

        while (rospy.Time.now().to_sec() < (start + self.compute_time)):
            for n in xrange(self.deltas.shape[0]):
                for traj_depth in xrange(self.rollouts.shape[1]):
                    delta_costs[n] += self.compute_cost(self.deltas[n],self.rollouts[n,traj_depth,:],msg)

        # Find the delta that has the smallest cost and execute it by publishing
        chosen_delta = np.argmin(delta_costs)

        # Setup the control message
        ads = AckermannDriveStamped()
        ads.header.frame_id = '/small_basement'
        ads.header.stamp = rospy.Time.now()
        ads.drive.steering_angle = self.deltas[chosen_delta]
        ads.drive.speed = self.speed

        # Publish the 
        self.cmd_pub.publish(ads)
'''
Apply the kinematic model to the passed pose and control
  pose: The current state of the robot [x, y, theta]
  control: The controls to be applied [v, delta, dt]
  car_length: The length of the car
Returns the resulting pose of the robot
'''
def kinematic_model_step(pose, control, car_length):
  x,y,theta = pose
  v,delta,dt = control
 
  if np.abs(delta) < 1e-2:
    dx = v*np.cos(theta)*dt
    dy = v*np.sin(theta)*dt
    dtheta = 0.0
  else:
    beta = np.arctan(0.5*np.tan(delta))
    sin2beta = np.sin(2*beta)
    dtheta = ((v/car_length) * sin2beta) * dt
    dx = (car_length/sin2beta)*(np.sin(theta+dtheta)-np.sin(theta))
    dy = (car_length/sin2beta)*(-1*np.cos(theta+dtheta)+np.cos(theta))
   
  while theta + dtheta > 2*np.pi:
    dtheta -= 2*np.pi
   
  while theta + dtheta < 2*np.pi:
    dtheta += 2*np.pi
   
  return np.array([x+dx, y+dy, theta+dtheta], dtype=np.float)
    
'''
Repeatedly apply the kinematic model to produce a trajectory for the car
  init_pose: The initial pose of the robot [x,y,theta]
  controls: A Tx3 numpy matrix where each row is of the form [v,delta,dt]
  car_length: The length of the car
Returns a Tx3 matrix where the t-th row corresponds to the robot's pose at time t+1
'''
def generate_rollout(init_pose, controls, car_length, T):
    # create an empty array for the trajectories
    trajectory_array = np.zeros((T,3), dtype=np.float)
    current_pose = np.array([0.0,0.0,0.0], dtype=np.float)

    # Loop that runs for all the elements in the control array, calls the kinematic_model_step
    # and adds the resulting pose to the trajectory_array
    for i in xrange(controls.shape[0]):
        trajectory_array[i,:] = kinematic_model_step(current_pose, controls[i,:], car_length)
        current_pose = trajectory_array[i,:]
    
    return trajectory_array
   
'''
Helper function to generate a number of kinematic car rollouts
    speed: The speed at which the car should travel
    min_delta: The minimum allowed steering angle (radians)
    max_delta: The maximum allowed steering angle (radians)
    delta_incr: The difference (in radians) between subsequent possible steering angles
    dt: The amount of time to apply a control for
    T: The number of time steps to rollout for
    car_length: The length of the car
Returns a NxTx3 numpy array that contains N rolled out trajectories, each
containing T poses. For each trajectory, the t-th element represents the [x,y,theta]
pose of the car at time t+1
'''
def generate_mpc_rollouts(speed, min_delta, max_delta, delta_incr, dt, T, car_length):

    deltas = np.arange(min_delta, max_delta, delta_incr)
    N = deltas.shape[0]

    init_pose = np.array([0.0,0.0,0.0], dtype=np.float)

    rollouts = np.zeros((N,T,3), dtype=np.float)
    
    for i in xrange(N):
        controls = np.zeros((T,3), dtype=np.float)
        controls[:,0] = speed
        controls[:,1] = deltas[i]
        controls[:,2] = dt
        rollouts[i,:,:] = generate_rollout(init_pose, controls, car_length, T)
    
    return rollouts, deltas

def main():

    rospy.init_node('laser_wanderer', anonymous=True)

    # Load these parameters from launch file
    # We provide suggested starting values of params, but you should
    # tune them to get the best performance for your system
    # Look at constructor of LaserWanderer class for description of each var
    # 'Default' values are ones that probably don't need to be changed (but you could for fun)
    # 'Starting' values are ones you should consider tuning for your system  
    # YOUR CODE HERE

    speed = rospy.get_param("~speed", 1.0)			        # Default val: 1.0
    min_delta = rospy.get_param("~min_delta", -0.34)		# Default val: -0.34
    max_delta = rospy.get_param("~max_delta", 0.341)		# Default val: 0.341
    delta_incr = rospy.get_param("~delta_inc", 0.113)		# Starting val: 0.34/3 (consider changing the denominator) 
    dt = rospy.get_param("~dt", 0.01)				        # Default val: 0.01
    T = rospy.get_param("~T", 300)				            # Starting val: 300
    compute_time = rospy.get_param("~compute_time", 0.09)	# Default val: 0.09
    laser_offset = rospy.get_param("~laser_offset", 1.0)	# Starting val: 1.0

    # DO NOT ADD THIS TO YOUR LAUNCH FILE, car_length is already provided by teleop.launch
    car_length = rospy.get_param("car_kinematics/car_length", 0.33)
    #car_width = 10

    # Generate the rollouts
    rollouts, deltas = generate_mpc_rollouts(speed, min_delta, max_delta, delta_incr, dt, T, car_length)

    # Create the LaserWanderer                                         
    lw = LaserWanderer(rollouts, deltas, speed, compute_time, laser_offset)

    # Keep the node alive
    rospy.spin()
  

if __name__ == '__main__':
    main()

