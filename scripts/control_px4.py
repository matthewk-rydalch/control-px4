#!/usr/bin/env python3

import asyncio
from mavsdk import System
from mavsdk.offboard import (OffboardError, PositionNedYaw, VelocityNedYaw)
from mavsdk import mocap
from mavsdk.mocap import (PositionBody,Quaternion,SpeedBody,AngularVelocityBody,Covariance)
from mavsdk.telemetry import FlightMode
import navpy
import rospy
import numpy as np
from scipy.spatial.transform import Rotation as R

from nav_msgs.msg import Odometry #May have issues since this is call the same thing as the mocap odom
from geometry_msgs.msg import Point
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import Bool

class CntrlPx4:
    def __init__(self):
        self.frameId = mocap.Odometry.MavFrame(1) #Local_FRD, the mocap ned frame says it is not supported
        self.qUpdate = Quaternion(1.0,0.0,0.0,0.0) #Right now there is no external orientation measurement.  Covariance is set very high.
        self.angularVelocityUpdate = AngularVelocityBody(0.0,0.0,0.0) #Right now there is no external orientation measurement.  Covariance is set very high.
        self.positionCommands = PositionNedYaw(0.0,0.0,0.0,0.0)
        self.feedForwardVelocity = VelocityNedYaw(0.0,0.0,0.0,0.0)
        self.prevOdomUpdateTime = 0.0
        self.estimateMsg = Odometry()
        self.meas1_received = False
        self.flightMode = 'none'
        self.offBoardOn = False
        self.sim = rospy.get_param('~sim', False)
        self.battery = 0
        self.in_air = 0
        self.arm_status = 0
        self.euler = 0
        self.health = 0
        self.landed = 0
        self.rc_status = 0
        self.RI2b = R.from_quat([0.0,0.0,0.0,1.0])
        self.systemAddress = rospy.get_param('~systemAddress', "serial:///dev/ttyUSB0:921600")

        self.estimate_pub_ = rospy.Publisher('estimate',Odometry,queue_size=5,latch=True)
        self.switch_integrators_pub_ = rospy.Publisher('switch_integrators',Bool,queue_size=5,latch=True)
        self.commands_sub_ = rospy.Subscriber('commands', Odometry, self.commandsCallback, queue_size=5)
        self.positiion_measurement_sub_ = rospy.Subscriber('external_measurement', Odometry, self.externalMeasurementCallback, queue_size=5)
        
        #rospy.spin()
    
    def commandsCallback(self,msg):
        self.positionCommands.north_m = msg.pose.pose.position.x
        self.positionCommands.east_m = msg.pose.pose.position.y
        self.positionCommands.down_m = msg.pose.pose.position.z
        self.feedForwardVelocity.north_m_s = msg.twist.twist.linear.x
        self.feedForwardVelocity.east_m_s = msg.twist.twist.linear.y
        self.feedForwardVelocity.down_m_s = msg.twist.twist.linear.z

    def externalMeasurementCallback(self,msg):
       time = np.array(msg.header.stamp.secs) + np.array(msg.header.stamp.nsecs*1E-9) #TODO this prossibly needs to be adjusted for the px4 time.
       time = int(round(time,6)*1E6)
       positionBodyUpdate = PositionBody(msg.pose.pose.position.x,msg.pose.pose.position.y,msg.pose.pose.position.z) #is this in the body frame as well?
       speedInertialUpdate = [msg.twist.twist.linear.x,msg.twist.twist.linear.y,msg.twist.twist.linear.z]
       speedBodyUpdateList = self.RI2b.apply(speedInertialUpdate)
       speedBodyUpdate = SpeedBody(speedBodyUpdateList[0],speedBodyUpdateList[1],speedBodyUpdateList[2]) #this one is represented in the body frame.
       poseCovarianceMatrix = self.convert_ros_covariance_to_px4_covariance(msg.pose.covariance)
       poseCovariance = Covariance(poseCovarianceMatrix)
       twistCovarianceMatrix = self.convert_ros_covariance_to_px4_covariance(msg.twist.covariance)
       twistCovariance = Covariance(twistCovarianceMatrix)
       self.odomUpdate = mocap.Odometry(time,self.frameId,positionBodyUpdate,self.qUpdate,speedBodyUpdate,self.angularVelocityUpdate,poseCovariance,twistCovariance)
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

       self.RI2b = R.from_quat([odom.q.x,odom.q.y,odom.q.z,odom.q.w])

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

    def switch_integrators(self,onOffFlag):
       flag = Bool()
       flag.data = onOffFlag
       self.switch_integrators_pub_.publish(flag)

    async def run(self):
        drone = System()
        print('system address = ', self.systemAddress)
        await drone.connect(system_address=self.systemAddress)

        print("Waiting for drone to connect...")
        await asyncio.sleep(5)
        async for state in drone.core.connection_state():
            if state.is_connected:
                print(f"Drone discovered with UUID: {state.uuid}")
                break

        #TODO publish all of these messages, so that rosbags contain this information
        await drone.telemetry.set_rate_odometry(100)
        # await drone.telemetry.set_rate_attitude(1) #doesn't seem to affect euler?
        await drone.telemetry.set_rate_battery(0.1)
        await drone.telemetry.set_rate_in_air(1)
        await drone.telemetry.set_rate_landed_state(1)
        await drone.telemetry.set_rate_rc_status(0.1)
        asyncio.ensure_future(self.flight_modes(drone))
        asyncio.ensure_future(self.input_meas_output_est(drone))
        asyncio.ensure_future(self.print_status(drone))
        asyncio.ensure_future(self.print_battery(drone))
        asyncio.ensure_future(self.print_in_air(drone))
        asyncio.ensure_future(self.print_armed(drone))
        # asyncio.ensure_future(self.print_euler(drone))
        asyncio.ensure_future(self.print_health(drone))
        asyncio.ensure_future(self.print_landed_state(drone))
        asyncio.ensure_future(self.print_rc_status(drone))

        if self.sim == True:
            await drone.action.arm()
            await drone.offboard.set_position_velocity_ned(PositionNedYaw(0.0,0.0,0.0,0.0),VelocityNedYaw(0.0, 0.0, 0.0, 0.0))
            await drone.offboard.start()
            print("Simulation starting offboard.")

        print('end of run')
        
    async def flight_modes(self,drone):
        async for flight_mode in drone.telemetry.flight_mode():
            if self.flightMode != flight_mode:
                print("FlightMode:", flight_mode)
                self.flightMode = flight_mode
                if flight_mode == FlightMode(7):
                    self.switch_integrators(True)
                    self.offBoardOn = True
                else:
                    self.switch_integrators(False)
                    self.offBoardOn = False

    async def input_meas_output_est(self,drone):
        async for odom in drone.telemetry.odometry():
            self.publish_estimate(odom)
            if self.odomUpdate.time_usec != self.prevOdomUpdateTime and self.meas1_received:
                await drone.mocap.set_odometry(self.odomUpdate)
                self.prevOdomUpdateTime = self.odomUpdate.time_usec
            await drone.offboard.set_position_velocity_ned(self.positionCommands,self.feedForwardVelocity)

    async def print_status(self,drone):
        async for status in drone.telemetry.status_text():
            print(status.type, status.text)

    async def print_battery(self,drone):
        async for battery in drone.telemetry.battery():
            if battery != self.battery:
                print(f"Battery: {battery}")
                self.battery = battery

    async def print_in_air(self,drone):
        async for in_air in drone.telemetry.in_air():
            if in_air != self.in_air:
                print(f"In air: {in_air}")
                self.in_air = in_air

    async def print_armed(self,drone):
        async for arm_status in drone.telemetry.armed():
            if arm_status != self.arm_status:
                print(f"Armed: {arm_status}")
                self.arm_status = arm_status

    async def print_euler(self,drone):
        async for euler in drone.telemetry.attitude_euler():
            if euler != self.euler:
                print(f"euler: {euler}")
                self.euler = euler

    async def print_health(self,drone):
        async for health in drone.telemetry.health_all_okay():
            if health != self.health:
                print(f"health all okay: {health}")
                self.health = health

    async def print_landed_state(self,drone):
        async for landed in drone.telemetry.landed_state():
            if landed != self.landed:
                print(f"landed: {landed}")
                self.landed = landed

    async def print_rc_status(self,drone):
        async for rc_status in drone.telemetry.rc_status():
            if rc_status != self.rc_status:
                print(f"rc status: {rc_status}")
                self.rc_status = rc_status

if __name__ == "__main__":
    rospy.init_node('control_px4', anonymous=True)
    try:
        cntrl_px4 = CntrlPx4()
        asyncio.ensure_future(cntrl_px4.run())
        loop = asyncio.get_event_loop().run_forever()
    except:
       rospy.ROSInterruptException
    pass
