#!/usr/bin/env python3

import asyncio
from mavsdk import System
from mavsdk.offboard import (OffboardError, VelocityNedYaw)
from mavsdk.mocap import (AttitudePositionMocap,VisionPositionEstimate,Quaternion,PositionBody,AngleBody,Covariance)
import navpy
import rospy
import numpy as np

from nav_msgs.msg import Odometry
from geometry_msgs.msg import Point
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import Bool

class ManualPx4:
    def __init__(self):
        self.prevPoseTime = 0.0
        self.estimateMsg = Odometry()
        self.meas1_received = False

        self.estimate_pub_ = rospy.Publisher('estimate',Odometry,queue_size=5,latch=True)
        self.positiion_measurement_sub_ = rospy.Subscriber('position_measurement', PoseWithCovarianceStamped, self.positionMeasurementCallback, queue_size=5)

    def positionMeasurementCallback(self,msg):
        time = np.array(msg.header.stamp.secs) + np.array(msg.header.stamp.nsecs*1E-9) #TODO this prossibly needs to be adjusted for the px4 time.
        time = int(round(time,6)*1E6)
        angleBody = AngleBody(0.0,0.0,0.0) #Currently no angle information is given
        positionBody = PositionBody(msg.pose.pose.position.x,msg.pose.pose.position.y,msg.pose.pose.position.z)
        covarianceMatrix = self.convert_ros_covariance_to_px4_covariance(msg.pose.covariance)
        poseCovariance = Covariance(covarianceMatrix)
        self.pose = VisionPositionEstimate(time,positionBody,angleBody,poseCovariance)
        self.meas1_received = True

    def convert_ros_covariance_to_px4_covariance(self,rosCov):
        px4Cov = [0]*21
        px4Cov[0:6] = rosCov[0:6]
        px4Cov[6:11] = rosCov[7:12]
        px4Cov[11:15] = rosCov[14:18]
        px4Cov[15:18] = rosCov[21:24]
        px4Cov[18:20] = rosCov[28:30]
        px4Cov[20] = rosCov[35]
        return px4Cov

    def publish_estimate(self,odom):
        time = odom.time_usec*1E-6
        secs = int(time)
        nsecs = int((time-secs)*1E9)

        self.estimateMsg.header.stamp.secs = secs
        self.estimateMsg.header.stamp.nsecs = nsecs
        self.estimateMsg.pose.pose.position.x = odom.position_body.x_m
        self.estimateMsg.pose.pose.position.y = odom.position_body.y_m
        self.estimateMsg.pose.pose.position.z = odom.position_body.z_m
        self.estimateMsg.pose.pose.orientation.x = odom.q.x
        self.estimateMsg.pose.pose.orientation.y = odom.q.y
        self.estimateMsg.pose.pose.orientation.z = odom.q.z
        self.estimateMsg.pose.pose.orientation.w = odom.q.w
        self.estimateMsg.pose.covariance = self.convert_px4_covariance_to_ros_covariance(odom.pose_covariance.covariance_matrix)
        self.estimateMsg.twist.twist.linear.x = odom.velocity_body.x_m_s
        self.estimateMsg.twist.twist.linear.y = odom.velocity_body.y_m_s
        self.estimateMsg.twist.twist.linear.z = odom.velocity_body.z_m_s
        self.estimateMsg.twist.twist.angular.x = odom.angular_velocity_body.roll_rad_s
        self.estimateMsg.twist.twist.angular.y = odom.angular_velocity_body.pitch_rad_s
        self.estimateMsg.twist.twist.angular.z = odom.angular_velocity_body.yaw_rad_s
        self.estimateMsg.twist.covariance = self.convert_px4_covariance_to_ros_covariance(odom.velocity_covariance.covariance_matrix)

        self.estimate_pub_.publish(self.estimateMsg)

    def convert_px4_covariance_to_ros_covariance(self,px4Cov):
        rosCov = [0]*36
        rosCov[0:6] = px4Cov[0:6]
        rosCov[7:12] = px4Cov[6:11]
        rosCov[14:18] = px4Cov[11:15]
        rosCov[21:24] = px4Cov[15:18]
        rosCov[28:30] = px4Cov[18:20]
        rosCov[35] = px4Cov[20]
        return rosCov

    async def run(self):
        """ Does Offboard control using velocity NED coordinates. """

        self.drone = System()
        await self.drone.connect(system_address="serial:///dev/ttyACM1")

        print("Waiting for drone to connect...")
        async for state in self.drone.core.connection_state():
            print('in for loop')
            if state.is_connected:
                print(f"Drone discovered with UUID: {state.uuid}")
                break

        print("Start updating position")
        await asyncio.sleep(5)
        asyncio.create_task(self.input_meas_output_est())
        await asyncio.sleep(10)

        print("Able to fly")

    async def input_meas_output_est(self):
        async for odom in self.drone.telemetry.odometry():
            self.publish_estimate(odom)
            if self.pose.time_usec != self.prevPoseTime and self.meas1_received:
                await self.drone.mocap.set_vision_position_estimate(self.pose)
                self.prevPoseTime = self.pose.time_usec

if __name__ == "__main__":
    rospy.init_node('manual_px4', anonymous=True)
    try:
        man_px4 = ManualPx4()
        asyncio.ensure_future(man_px4.run())
        loop = asyncio.get_event_loop()
        loop.run_forever()
    except:
        rospy.ROSInterruptException
    pass