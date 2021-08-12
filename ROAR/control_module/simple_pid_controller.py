from ROAR.control_module.controller import Controller
from ROAR.utilities_module.data_structures_models import Transform
from ROAR.utilities_module.vehicle_models import Vehicle
from ROAR.utilities_module.vehicle_models import VehicleControl
from collections import deque
import numpy as np


class SimplePIDController(Controller):
    def __init__(self, agent, **kwargs):
        super().__init__(agent, **kwargs)
        self.lat_error_queue = deque(maxlen=100)  # this is how much error you want to accumulate
        self.long_error_queue = deque(maxlen=100)  # this is how much error you want to accumulate

        self.target_speed = 1.5  # m / s
        self.min_throttle, self.max_throttle = 0, 0.5

        self.lat_kp = 0.0025  # this is how much you want to steer
        self.lat_kd = 0.01  # this is how much you want to resist change
        self.lat_ki = 0.00001  # this is the correction on past error

        self.long_kp = 0.14  # this is how much you want to go forward
        self.long_kd = 0  # this is how much you want to resist change
        self.long_ki = 0  # this is how much correction on past error

    def run_in_series(self, next_waypoint=None, **kwargs) -> VehicleControl:
        steering = self.lateral_pid_control()
        throttle = self.long_pid_control()
        return VehicleControl(throttle=throttle, steering=steering)

    def lateral_pid_control(self) -> float:
        error = self.agent.kwargs.get("lat_error", 0)
        self.lat_error_queue.append(error)
        error_dt = 0 if len(self.lat_error_queue) == 0 else error - self.lat_error_queue[-1]
        error_it = sum(self.lat_error_queue)

        e_p = self.lat_kp * error
        e_d = self.lat_kd * error_dt
        e_i = self.lat_ki * error_it
        lat_control = np.clip((e_p + e_d + e_i), -1, 1)
        print(round(e_p, 3), round(e_d,3), round(e_i, 3), round(lat_control,3))

        return lat_control

    def long_pid_control(self) -> float:
        curr_speed = Vehicle.get_speed(self.agent.vehicle)
        error = curr_speed - self.target_speed
        error_dt = 0 if len(self.long_error_queue) == 0 else error - self.long_error_queue[-1]
        error_it = sum(self.long_error_queue)

        e_p = self.long_kp * error
        e_d = self.long_kd * error_dt
        e_i = self.long_ki * error_it
        long_control = np.clip(-1 * (e_p + e_d + e_i), self.min_throttle, self.max_throttle)
        return long_control
