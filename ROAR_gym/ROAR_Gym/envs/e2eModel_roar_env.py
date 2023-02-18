try:
    from ROAR_Gym.envs.roar_env import ROAREnv
except:
    from ROAR_gym.ROAR_Gym.envs.roar_env import ROAREnv

from ROAR.utilities_module.vehicle_models import VehicleControl
from ROAR.agent_module.agent import Agent
from ROAR.utilities_module.vehicle_models import Vehicle
from typing import Tuple
import numpy as np
from typing import List, Any
import gym
import math
from collections import OrderedDict
from gym.spaces import Discrete, Box
import cv2
import wandb
import skimage.measure

# imports for reading and writing json config files
from ROAR_gym.utility import json_read_write, next_spawn_point

# Load spawn parameters from the ppo_configuration file
from ROAR_gym.configurations.ppo_configuration import spawn_params

mode='baseline'
FRAME_STACK = 4
CONFIG = {
    "x_res": 84,
    "y_res": 84
}

spawn_params["spawn_int_map"] = np.array([39, 91, 140, 224, 312, 442, 556, 730, 782, 898, 1142, 1283, 0])

class ROARppoEnvE2E(ROAREnv):
    def __init__(self, params):
        super().__init__(params)
        low=np.array([-2.5, -4.0, 1.0])
        high=np.array([-0.5, 4.0, 3.0])
        self.mode=mode
        self.action_space = Box(low=low, high=high, dtype=np.float32)

        self.observation_space = Box(-10, 1, shape=(FRAME_STACK,2, CONFIG["x_res"], CONFIG["y_res"]), dtype=np.float32)
        self.prev_speed = 0
        self.prev_cross_reward = 0
        self.crash_check = False
        self.ep_rewards = 0
        self.frame_reward = 0
        self.highscore = -1000
        self.highest_chkpt = 0
        self.speeds = []
        self.prev_int_counter = 0
        self.steps=0
        self.largest_steps=0
        self.highspeed=0
        self.complete_loop=False
        self.his_checkpoint=[]
        self.his_score=[]
        self.time_to_waypoint_ratio = 5.0 #0.75
        self.fps = 32
        self.death_line_dis = 5
        ## used to check if stalled
        self.stopped_counter = 0
        self.stopped_max_count = 100
        # used to track episode highspeed
        self.speed = 0
        self.current_hs = 0
        # used to check laptime
        if self.carla_runner.world is not None:
            self.last_sim_time = self.carla_runner.world.hud.simulation_time
        else:
            self.last_sim_time = 0
        self.sim_lap_time = 0

        self.deadzone_trigger = True
        self.deadzone_level = 0.001
        self.overlap = False

        # Spawn initializations
        # TODO: This is a hacky fix because the reset function seems to be called on init as well.
        if spawn_params["dynamic_type"] == "linear forward":
            self.agent_config.spawn_point_id = spawn_params["init_spawn_pt"] - 1
        elif spawn_params["dynamic_type"] == "linear backward":
            self.agent_config.spawn_point_id = spawn_params["init_spawn_pt"] + 1
        elif spawn_params["dynamic_type"] == "uniform random":
            self.agent_config.spawn_point_id = np.random.randint(low=1, high=12)
        else:
            self.agent_config.spawn_point_id = spawn_params["init_spawn_pt"]

        self.agent.spawn_counter = spawn_params["spawn_int_map"][self.agent_config.spawn_point_id]
        print("#########################\n",self.agent.spawn_counter)


    def step(self, action: Any) -> Tuple[Any, float, bool, dict]:
        obs = []
        rewards = []
        self.steps += 1

        action = action.reshape((-1))
        throttle = (action[0] + 0.5) / 2 + 1
        braking = (action[2] - 1.0) / 2

        # full_throttle_thre = 0.6
        # non_braking_thre = 0.4
        # throttle = min(1, throttle_check / full_throttle_thre)
        # braking = max(0, (braking_check - non_braking_thre) / (1 - non_braking_thre))

        # check = (action[0] + 0.5) / 2 + 1
        # if check > 0.5:
        #     throttle = 0.7
        #     braking = 0
        # else:
        #     throttle = 0
        #     braking = 0.8

        steering = action[1] / 4

        if self.deadzone_trigger and abs(steering) < self.deadzone_level:
            steering = 0.0


        self.agent.kwargs["control"] = VehicleControl(throttle=throttle,
                                                        steering=steering,
                                                        braking=braking)

        ob, reward, is_done, info = super(ROARppoEnvE2E, self).step(action)


        obs.append(ob)
        rewards.append(reward)

        self.render()
        self.frame_reward = sum(rewards)
        self.ep_rewards += sum(rewards)

        self.speed = self.agent.vehicle.get_speed(self.agent.vehicle)
        if self.speed > self.current_hs:
            self.current_hs = self.speed

        if is_done:
            self.wandb_logger()
            self.crash_check = False
            self.update_highscore()
        return np.array(obs), self.frame_reward, self._terminal(), self._get_info()

    def _get_info(self) -> dict:
        info_dict = OrderedDict()
        info_dict["Current HIGHSCORE"] = self.highscore
        info_dict["Furthest Checkpoint"] = self.highest_chkpt*self.agent.interval
        info_dict["episode reward"] = self.ep_rewards
        info_dict["checkpoints"] = self.agent.int_counter*self.agent.interval
        info_dict["reward"] = self.frame_reward
        info_dict["largest_steps"] = self.largest_steps
        info_dict["current_hs"] = self.current_hs
        info_dict["highest_speed"] = self.highspeed
        info_dict["complete_state"]=self.complete_loop
        info_dict["avg10_checkpoints"]=np.average(self.his_checkpoint)
        info_dict["avg10_score"]=np.average(self.his_score)
        # info_dict["throttle"] = action[0]
        # info_dict["steering"] = action[1]
        # info_dict["braking"] = action[2]
        return info_dict

    def update_highscore(self):
        if self.ep_rewards > self.highscore:
            self.highscore = self.ep_rewards
        if self.agent.int_counter > self.highest_chkpt:
            self.highest_chkpt = self.agent.int_counter
        if self.current_hs > self.highspeed:
            self.highspeed = self.current_hs
        self.current_hs = 0

        if self.carla_runner.world is not None:
            current_time = self.carla_runner.world.hud.simulation_time
            if self.agent.int_counter * self.agent.interval < 27180:
                self.sim_lap_time = 400
            else:
                self.sim_lap_time = current_time - self.last_sim_time
            self.last_sim_time = current_time
        else:
            self.sim_lap_time = 0
            self.last_sim_time = 0
        return

    def _terminal(self) -> bool:
        if self.stopped_counter >= self.stopped_max_count:
            print("what")
            return True
        if self.carla_runner.get_num_collision() > self.max_collision_allowed:
            print("man")
            return True
        elif self.crash_check: #elif self.overlap:
            print("pls")
            return True
        # elif self.overlap:
        #     print("overlap--------------------------------------------------------------")
        #     return True
        elif self.agent.finish_loop:
            print("halp")
            self.complete_loop=True
            return True
        else:
            return False

    def get_reward(self) -> float:
        reward = -1

        
        if abs(self.agent.vehicle.control.steering) <= 0.1:
            reward += 0.1

        if self.crash_check:
            print("no reward")
            return 0

        if self.agent.cross_reward > self.prev_cross_reward:
            reward += (self.agent.cross_reward - self.prev_cross_reward)*self.agent.interval*self.time_to_waypoint_ratio

        if not (self.agent.bbox_list[(self.agent.int_counter - self.death_line_dis) % len(self.agent.bbox_list)].has_crossed(self.agent.vehicle.transform))[0]:
            reward -= 200
            self.crash_check = True
        elif self.carla_runner.get_num_collision() > 0 or self.overlap:
            reward -= 200
            self.crash_check = True

        # if self.agent.int_counter > 1 and self.agent.vehicle.get_speed(self.agent.vehicle) < 1:
        #     self.stopped_counter += 1
        #     if self.stopped_counter >= self.stopped_max_count:
        #         reward -= 200
        #         self.crash_check = True

        


        # log prev info for next reward computation
        self.prev_speed = Vehicle.get_speed(self.agent.vehicle)
        self.prev_cross_reward = self.agent.cross_reward
        return reward

    def _get_obs(self) -> np.ndarray:
        if mode=='baseline':
            index_from=(self.agent.int_counter%len(self.agent.bbox_list))
            if index_from+10<=len(self.agent.bbox_list):
                # print(index_from,len(self.agent.bbox_list),index_from+10-len(self.agent.bbox_list))
                next_bbox_list=self.agent.bbox_list[index_from:index_from+10]
            else:
                # print(index_from,len(self.agent.bbox_list),index_from+10-len(self.agent.bbox_list))
                next_bbox_list=self.agent.bbox_list[index_from:]+self.agent.bbox_list[:index_from+10-len(self.agent.bbox_list)]
            assert(len(next_bbox_list)==10)
            map_list,overlap = self.agent.occupancy_map.get_map_baseline(transform_list=self.agent.vt_queue,
                                                    view_size=(CONFIG["x_res"], CONFIG["y_res"]),
                                                    bbox_list=self.agent.frame_queue,
                                                                 next_bbox_list=next_bbox_list
                                                    )
            self.overlap=overlap
            # data = cv2.resize(occu_map, (CONFIG["x_res"], CONFIG["y_res"]), interpolation=cv2.INTER_AREA)
            #cv2.imshow("Occupancy Grid Map", cv2.resize(np.float32(data), dsize=(500, 500)))

            # data_view=np.sum(data,axis=2)
            # wall=self.agent.occupancy_map.get_wall(transform=self.agent.vt_queue[-1],
            #                                         view_size=(CONFIG["x_res"], CONFIG["y_res"]))
            # wall2=self.agent.occupancy_map.get_wall(transform=self.agent.vt_queue[-1],
            #                                         view_size=(CONFIG["x_res"]*2, CONFIG["y_res"]*2))
            
            # wall2=skimage.measure.block_reduce(wall2, (2,2), np.max)
            # wall4=self.agent.occupancy_map.get_wall(transform=self.agent.vt_queue[-1],
            #                                         view_size=(CONFIG["x_res"]*4, CONFIG["y_res"]*4))
            
            # wall4=skimage.measure.block_reduce(wall4, (4,4), np.max)
            # wall8=self.agent.occupancy_map.get_wall(transform=self.agent.vt_queue[-1],
            #                                         view_size=(CONFIG["x_res"]*8, CONFIG["y_res"]*8))
            
            # wall8=skimage.measure.block_reduce(wall8, (8,8), np.max)
            # print(wall.shape,wall2.shape,wall4.shape,wall8.shape)
            # map_list4,_ = self.agent.occupancy_map.get_map_baseline(transform_list=self.agent.vt_queue,
            #                                         view_size=(CONFIG["x_res"]*4, CONFIG["y_res"]*4),
            #                                         bbox_list=self.agent.frame_queue,
            #                                         next_bbox_list=next_bbox_list
            #                                         )
            # map_list4=skimage.measure.block_reduce(map_list4, (1,1,4,4), np.max)
            map_list=map_list[:,-1:]
            # wall_list=np.array([[wall],[wall2],[wall4],[wall8]])
            wall_list=self.agent.occupancy_map.get_wall1248(transform=self.agent.vt_queue[-1],
                                                    view_size=(CONFIG["x_res"], CONFIG["y_res"]))
            # print([x.shape for x in wall_list])

            wall_list=np.array([[skimage.measure.block_reduce(wall_list[i], ([1,2,4,8][i],[1,2,4,8][i]), np.max)] for i in range(len(wall_list))])
            # print(map_list.shape,wall_list.shape)
            map_list=np.hstack((map_list,wall_list))
            cv2.imshow("data", np.hstack(np.hstack(map_list))) # uncomment to show occu map
            cv2.waitKey(1)
            #print(mapList.shape,'------------------------------------------------------------------------------------------------------------------------')
            return map_list

        else:
            data = self.agent.occupancy_map.get_map(transform=self.agent.vehicle.transform,
                                                    view_size=(CONFIG["x_res"], CONFIG["y_res"]),
                                                    arbitrary_locations=self.agent.bbox.get_visualize_locs(),
                                                    arbitrary_point_value=self.agent.bbox.get_value(),
                                                    vehicle_velocity=self.agent.vehicle.velocity,
                                                    # rotate=self.agent.bbox.get_yaw()
                                                    )
            # data = cv2.resize(occu_map, (CONFIG["x_res"], CONFIG["y_res"]), interpolation=cv2.INTER_AREA)
            #cv2.imshow("Occupancy Grid Map", cv2.resize(np.float32(data), dsize=(500, 500)))

            # data_view=np.sum(data,axis=2)
            cv2.imshow("data", data) # uncomment to show occu map
            cv2.waitKey(1)
            # yaw_angle=self.agent.vehicle.transform.rotation.yaw
            # velocity=self.agent.vehicle.get_speed(self.agent.vehicle)
            # data[0,0,2]=velocity
            data_input=data.copy()
            data_input[data_input==1]=-10
            return data_input  # height x width x 3 array
    #3location 3 rotation 3velocity 20 waypoline locations 20 wayline rewards

    def reset(self) -> Any:
        if len(self.his_checkpoint)>=10:
            self.his_checkpoint=self.his_checkpoint[-10:]
            self.his_score=self.his_score[-10:]
        if self.agent:
            self.his_checkpoint.append(self.agent.int_counter*self.agent.interval)
            self.his_score.append(self.ep_rewards)
        self.ep_rewards = 0
        self.stopped_counter = 0
        if self.steps>self.largest_steps and not self.complete_loop:
            self.largest_steps=self.steps
        elif self.complete_loop and self.agent.finish_loop and self.steps<self.largest_steps:
            self.largest_steps=self.steps

        # Change Spawn Point before reset
        self.agent_config.spawn_point_id = next_spawn_point(self.agent_config.spawn_point_id)
        print("Spawn Pt ID", self.agent_config.spawn_point_id)
        self.EgoAgentClass.spawn_counter = spawn_params["spawn_int_map"][self.agent_config.spawn_point_id]
        self.agent.spawn_counter = spawn_params["spawn_int_map"][self.agent_config.spawn_point_id]

        super(ROARppoEnvE2E, self).reset()
        self.agent.spawn_counter = spawn_params["spawn_int_map"][self.agent_config.spawn_point_id]
        print(self.agent.spawn_counter)
        self.steps=0
        self.agent.kwargs["control"] = VehicleControl(throttle=1.0,
                                                            steering=0.0,
                                                            braking=0.0)
        for _ in range(80):
            print('step '+str(self.steps))
            super(ROARppoEnvE2E, self).step(None)
            self.steps+=1
        # self.crash_step=0
        # self.reward_step=0
        return self._get_obs()

    def wandb_logger(self):
        wandb.log({
            "Episode reward": self.ep_rewards,
            "Checkpoint reached": self.agent.int_counter*self.agent.interval,
            "largest_steps" : self.largest_steps,
            "highest_speed" : self.highspeed,
            "Episode_Sim_Time": self.sim_lap_time,
            "episode Highspeed": self.current_hs,
            "avg10_checkpoints":np.average(self.his_checkpoint),
            "avg10_score":np.average(self.his_score),
        })
        return