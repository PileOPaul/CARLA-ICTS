"""
Author: Dikshant Gupta
Time: 23.03.21 14:27
"""
import sys
import random
from benchmark.environment.ped_controller import SON, ICR, ControllerConfig, InternalStateSetter, LeanForward, LookBehindLeft, LookBehindLeftSpine, LookBehindRight, PathController, Relaxer, ResetPose, TurnHeadLeftWalk, TurnHeadRightBehind, TurnHeadRightWalk, l2_length, y_distance
from benchmark.environment.ped_controller import l2_distance
from benchmark.environment.utils import find_weather_presets
from benchmark.environment.sensors import *
import carla
import timeit

class World(object):
    def __init__(self, carla_world, hud, scenario, args):
        self.world = carla_world
        self.actor_role_name = args.rolename
        try:
            self.map = self.world.get_map()
        except RuntimeError as error:
            print('RuntimeError: {}'.format(error))
            print('  The server could not send the OpenDRIVE (.xodr) file:')
            print('  Make sure it exists, has the same name of your town, and is correct.')
            sys.exit(1)
        self.hud = hud
        self.scenario = None
        self.player = None
        self.walker = None
        self.incoming_car = None
        self.parked_cars = None
        self.player_max_speed = None
        self.player_max_speed_fast = None
        self.collision_sensor = None
        self.lane_invasion_sensor = None
        self.gnss_sensor = None
        self.imu_sensor = None
        self.radar_sensor = None
        self.camera_manager = None
        self.semseg_sensor = None
        self._weather_presets = find_weather_presets()
        self._weather_index = 0
        self._actor_filter = args.filter
        self._gamma = args.gama
        self.recording_enabled = False
        self.recording_start = 0
        self.constant_velocity_enabled = False
        self.current_map_layer = 0
        self.map_layer_names = [
            carla.MapLayer.NONE,
            carla.MapLayer.Buildings,
            carla.MapLayer.Decals,
            carla.MapLayer.Foliage,
            carla.MapLayer.Ground,
            carla.MapLayer.ParkedVehicles,
            carla.MapLayer.Particles,
            carla.MapLayer.Props,
            carla.MapLayer.StreetLights,
            carla.MapLayer.Walls,
            carla.MapLayer.All
        ]

        self.car_blueprint = self.get_car_blueprint()
        self.ped_speed = None
        self.ped_distance = None
        self.drawn = False
        self.camera = True
        self.restart(scenario)
        self.world.on_tick(hud.on_world_tick)
        for _ in range(2):
            self.next_weather()
        self.random = False
        self.debug = False


    def get_car_blueprint(self):
        blueprint = random.choice(self.world.get_blueprint_library().filter(self._actor_filter))
        blueprint.set_attribute('role_name', self.actor_role_name)
        if blueprint.has_attribute('color'):
            color = blueprint.get_attribute('color').recommended_values[1]
            blueprint.set_attribute('color', color)
        if blueprint.has_attribute('driver_id'):
            driver_id = random.choice(blueprint.get_attribute('driver_id').recommended_values)
            blueprint.set_attribute('driver_id', driver_id)
        if blueprint.has_attribute('is_invincible'):
            blueprint.set_attribute('is_invincible', 'true')
        # set the max speed
        if blueprint.has_attribute('speed'):
            self.player_max_speed = float(blueprint.get_attribute('speed').recommended_values[1])
            self.player_max_speed_fast = float(blueprint.get_attribute('speed').recommended_values[2])
        else:
            print("No recommended values for 'speed' attribute")
        return blueprint

    def get_random_blueprint(self):
        vehicles = ["vehicle.audi.a2", "vehicle.audi.tt", "vehicle.chevrolet.impala", "vehicle.audi.etron"]
        vehicle_type = random.choice(vehicles)
        blueprint = random.choice(self.world.get_blueprint_library().filter(vehicle_type))
        blueprint.set_attribute('role_name', self.actor_role_name)
        if blueprint.has_attribute('color'):
            color = random.choice(blueprint.get_attribute('color').recommended_values)
            blueprint.set_attribute('color', color)
        if blueprint.has_attribute('driver_id'):
            driver_id = random.choice(blueprint.get_attribute('driver_id').recommended_values)
            blueprint.set_attribute('driver_id', driver_id)
        if blueprint.has_attribute('is_invincible'):
            blueprint.set_attribute('is_invincible', 'true')
        return blueprint

    def _draw_grid(self):
        width = 20
        world = self.world
        loc = self.walker.get_location()
        loc = self.walker.get_location() + carla.Location(0,0,0)
        upper = loc+carla.Location(0,-width,0)
        right = loc+carla.Location(width,0,0)
        right_upper = loc+carla.Location(width,-width,0)
        world.debug.draw_line(loc,upper,thickness=0.02)
        world.debug.draw_line(loc,right,thickness=0.02)
        world.debug.draw_line(upper,right_upper,thickness=0.02)
        world.debug.draw_line(right,right_upper,thickness=0.02)
        for i in range(1,width):
            offset_y = carla.Location(0,-i,0)
            offset_x = carla.Location(i,0,0)
            world.debug.draw_line(loc+offset_y,right+offset_y,thickness=0.02)
            world.debug.draw_line(loc+offset_x,upper+offset_x,thickness=0.02)

    def _draw_point(self, p, color=carla.Color(r=0,g=255,b=255)):
        self.world.debug.draw_point(p, size=0.1, color=color, life_time=0)

    def get_point(self,offset):
        cur = self.walker.get_location()
        offset_x, offset_y = offset
        loc = carla.Location(cur.x+offset_x, cur.y-offset_y,0.5)
        return loc

    def restart(self, scenario, conf=ControllerConfig()):
        self.scenario = scenario
        self.ped_speed = conf.ped_speed
        self.ped_distance = conf.ped_distance
        self.counter = 0

        # Keep same camera config if the camera manager exists.
        cam_index = self.camera_manager.index if self.camera_manager is not None else 0
        semseg_index = self.semseg_sensor.index if self.semseg_sensor is not None else 5
        cam_pos_index = self.camera_manager.transform_index if self.camera_manager is not None else 5
        semseg_pos_index = self.semseg_sensor.transform_index if self.semseg_sensor is not None else 5
        


        # Spawn the player.
        start = self.scenario[3]
        spawn_point = carla.Transform()
        spawn_point.location.x = start[0]
        spawn_point.location.y = start[1]
        spawn_point.location.z = 0.01
        spawn_point.rotation.yaw = start[2]

        
        if self.player is not None:
            self.destroy()
            self.player = self.world.try_spawn_actor(self.car_blueprint, spawn_point)
            self.modify_vehicle_physics(self.player)
        while self.player is None:
            if not self.map.get_spawn_points():
                print('There are no spawn points available in your map/town.')
                print('Please add some Vehicle Spawn Point to your UE4 scene.')
                sys.exit(1)
            self.player = self.world.try_spawn_actor(self.car_blueprint, spawn_point)
            #self.world.wait_for_tick()
            self.modify_vehicle_physics(self.player)

        # Set up other agents
        scenario_type = self.scenario[0]
        obstacles = self.scenario[1]
        #print(obstacles[0][1])
        # TODO remove code duplication
        if scenario_type == "01_int":
            self.choice = None
            self.setup_01_int(obstacles, conf)
            cam_transform = carla.Transform(
                carla.Location(spawn_point.location.x,
                        spawn_point.location.y-25,
                        spawn_point.location.z+7),
                carla.Rotation(-30,270,0))
            self.world.get_spectator().set_transform(cam_transform)
            #p = self.player.get_location()
            #print(p)
            #self.world.debug.draw_point(p+carla.Location(0,-2,2), size=0.1, color=carla.Color(r=0,g=255,b=255), life_time=0)
            if not self.random:
                self.player.set_target_velocity(carla.Vector3D(0,-6,0))
        elif scenario_type == "02_int":
            self.choice = None
            self.stopped = False
            self.setup_02_int(obstacles, conf)
            #print(spawn_point.location)
            cam_transform = carla.Transform(
                carla.Location(spawn_point.location.x,
                        spawn_point.location.y-35,
                        spawn_point.location.z+7),
                carla.Rotation(-30,270,0))
            self.world.get_spectator().set_transform(cam_transform)
            if not self.random:
                self.player.set_target_velocity(carla.Vector3D(0,-6,0))
        elif scenario_type == "03_int":
            self.choice = None
            self.stopped = False
            self.setup_03_int(obstacles, conf)
            cam_transform = carla.Transform(
                carla.Location(spawn_point.location.x,
                        spawn_point.location.y-20,
                        spawn_point.location.z+7),
                carla.Rotation(-30,270,0))
            self.world.get_spectator().set_transform(cam_transform)
            if not self.random:
                self.player.set_target_velocity(carla.Vector3D(0,-6,0))
        elif scenario_type == "01_non_int":
            self.choice = None
            self.stopped = False
            self.setup_01_non_int(obstacles, conf)
            cam_transform = carla.Transform(
                carla.Location(spawn_point.location.x,
                        spawn_point.location.y+-10,
                        spawn_point.location.z+5),
                carla.Rotation(-30,270,0))
            self.world.get_spectator().set_transform(cam_transform)
            if not self.random:
                self.player.set_target_velocity(carla.Vector3D(0,-6,0))
        elif scenario_type == "02_non_int":
            self.choice = None
            self.stopped = False
            self.setup_02_non_int(obstacles, conf)
            cam_transform = carla.Transform(
                carla.Location(spawn_point.location.x,
                        spawn_point.location.y+5,
                        spawn_point.location.z+15),
                carla.Rotation(-30,270,0))
            self.world.get_spectator().set_transform(cam_transform)
            if not self.random:
                self.player.set_target_velocity(carla.Vector3D(0,-6,0))
        elif scenario_type == "03_non_int":
            self.choice = None
            self.stopped = False
            self.setup_03_non_int(obstacles, conf)
            cam_transform = carla.Transform(
                carla.Location(spawn_point.location.x,
                        spawn_point.location.y-20,
                        spawn_point.location.z+15),
                carla.Rotation(-30,270,0))
            self.world.get_spectator().set_transform(cam_transform)
            if not self.random:
                self.player.set_target_velocity(carla.Vector3D(0,-6,0))
        elif scenario_type in [1, 2, 4, 5]:
            # Single pedestrian scenarios
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
            self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, self.ped_speed, 0), 1))
            cam_transform = carla.Transform(
                carla.Location(spawn_point.location.x,
                        spawn_point.location.y+5,
                        spawn_point.location.z+15),
                carla.Rotation(-30,270,0))
            self.world.get_spectator().set_transform(cam_transform)
        elif scenario_type == 6:
            # Single pedestrian scenarios
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
            self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, -self.ped_speed, 0), 1))
        elif scenario_type in [3, 7, 8]:
            # Single pedestrian scenarios with parked car
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
            self.incoming_car = self.world.try_spawn_actor(obstacles[1][0], obstacles[1][1])
        elif scenario_type == 10:
            # Single pedestrian with incoming car
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
            self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, -self.ped_speed, 0), 1))
            self.incoming_car = self.world.try_spawn_actor(obstacles[1][0], obstacles[1][1])
        elif scenario_type == 9:
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
            self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), 1))
        elif scenario_type == 11:
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
            self.player.set_target_velocity(carla.Vector3D(0, 20 * 0.2778, 0))
            self.incoming_car = self.world.try_spawn_actor(obstacles[1][0], obstacles[1][1])
            self.parked_cars = []
            car_spawn_point = obstacles[2][1]
            car_spawn_point.location.y -= 7
            for _ in range(12):
                car_spawn_point.location.y += 7
                parked_car = None
                while parked_car is None:
                    parked_car = self.world.try_spawn_actor(self.get_random_blueprint(), car_spawn_point)
                self.parked_cars.append(parked_car)
        elif scenario_type == 12:
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
            self.incoming_car = self.world.try_spawn_actor(obstacles[1][0], obstacles[1][1])
            self.parked_cars = []
            parked_car = None
            while parked_car is None:
                parked_car = self.world.try_spawn_actor(obstacles[2][0], obstacles[2][1])
            self.parked_cars.append(parked_car)
        
        elif scenario_type == 0:
            self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])

        # Set up the sensors.
        ### Set walker flags ##
        self.walker.on_street = False

        if scenario_type in ["01_int", "02_int", "03_int"]:
            self.walker.icr = ICR.LOW
            self.walker.son = SON.AVERTING
        else:
            self.walker.icr = ICR.INTERESTED
            self.walker.son = SON.YIELDING
        #self.walker.initial_son = "Test"
        #######################
        self.collision_sensor = CollisionSensor(self.player, self.hud)
        self.lane_invasion_sensor = LaneInvasionSensor(self.player, self.hud)
        self.gnss_sensor = GnssSensor(self.player)
        self.imu_sensor = IMUSensor(self.player)
        if self.camera:
            self.camera_manager = CameraManager(self.player, self.hud, self._gamma)
            self.camera_manager.transform_index = cam_pos_index
            self.camera_manager.set_sensor(cam_index, notify=True, force_respawn=True)
            actor_type = get_actor_display_name(self.player)
            self.hud.notification(actor_type)

        self.semseg_sensor = CameraManager(self.player, self.hud, self._gamma)
        self.semseg_sensor.transform_index = semseg_pos_index
        self.semseg_sensor.set_sensor(semseg_index, notify=False)


    def tick(self, clock):
        self.counter +=1
        self.hud.tick(self, clock)
        dist_walker = abs(self.player.get_location().y - self.walker.get_location().y)
        car_velocity = self.player.get_velocity()
        car_speed = np.sqrt(car_velocity.x ** 2 + car_velocity.y ** 2)
        if not self.drawn:
            self.drawn = True
        if dist_walker < self.ped_distance:  # and car_speed > 0:
            if self.scenario[0] in [1, 2, 3]:
                self.walker.apply_control(carla.WalkerControl(carla.Vector3D(self.ped_speed, 0, 0), 1))
                if self.scenario[0] in [1, 3] and self.walker.get_location().x > 4.5:
                    self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), 1))
                if self.scenario[0] == 2 and self.walker.get_location().x > 95.0:
                    self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), 1))
            elif self.scenario[0] in [4, 5, 7, 8, 6]:
                self.walker.apply_control(carla.WalkerControl(carla.Vector3D(-self.ped_speed, 0, 0), 1))
                if self.walker.get_location().x < -4.5 and self.scenario[0] in [4, 7, 8]:
                    self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), 1))
                if self.scenario[0] in [5, 6] and self.walker.get_location().x < 85.0:
                    self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), 1))
            elif self.scenario[0] == 10:
                self.walker.apply_control(carla.WalkerControl(carla.Vector3D(-self.ped_speed, 0, 0), 1))
            elif self.scenario[0] == 9:
                self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, self.ped_speed, 0), 1))
        if self.scenario[0] == 10:
            flag = (0 < (self.walker.get_location().y - self.incoming_car.get_location().y) < 5) and \
                   (self.walker.get_location().x > -4.4)
            if self.incoming_car.get_location().y > 250 or flag:
                self.incoming_car.set_target_velocity(carla.Vector3D(0, 0, 0))
            else:
                self.incoming_car.set_target_velocity(carla.Vector3D(0, 9, 0))  # Set target velocity for experiment
        if self.scenario[0] == 11:
            # self.incoming_car.set_target_velocity(carla.Vector3D(0, -20 * 0.2778, 0))
            if self.incoming_car.get_location().y - self.player.get_location().y < 10:
                self.incoming_car.set_target_velocity(carla.Vector3D(0, 0, 0))
            else:
                self.incoming_car.set_target_velocity(carla.Vector3D(0, -self.ped_speed * 0.2778, 0))
        if self.scenario[0] == 12:
            # pass
            self.incoming_car.set_target_velocity(carla.Vector3D(0, self.ped_speed * 0.2778, 0))
        if self.scenario[0] == "01_int":
            status = self.path_controller_1.step()
            self.look_behind_right.step()
            self.turn_head.step()

            if self.choice == "Left":
                self.look_behind_left.step()
                if status=="Done":
                    self.reset.step()
                    self.path_controller_3.step()
            elif self.choice == "Right":
                self.reset.step()
                if status=="Done":
                    self.path_controller_2.step()
            else:
                if l2_distance(self.walker.get_location(), self.desc_p)<0.1:    
                    distance = y_distance(self.walker.get_location(), self.player.get_location())-2
                    #distance = l2_distance(self.walker.get_location(), self.player.get_location())
                    #print(distance)
                    #self.compute_collision_point()
                    if self.decision_trigger(distance,self.db):
                        self.choice = "Left"
                        self.walker.icr = ICR.VERY_LOW
                        self.walker.son = SON.AVERTING
                    else:
                        self.choice = "Right"
                        self.walker.icr = ICR.GOING_TO
                        #self.walker.con = SON.YIELDING 
            self.relaxer.step()
            self.iss_crossed.step()

        if self.scenario[0] == "02_int":
            #print(self.walker.get_control().speed)
            status = self.path_controller_1.step()
            self.turn_head.step()
            self.look_right.step()
            if self.choice == "Stop" :
                if status == "Done" and not self.stopped:
                    self.set_walker_speed_relative(0.0)
                    self.stopped = True
                    if self.choice == "Stop":
                        self.path_controller_2.cur_speed = self.path_controller_1.cur_speed
                    else:
                        self.path_controller_2.cur_speed = self.path_controller_1.cur_speed
                    self.path_controller_2.speed_schedule = self.speed_schedule_stop
                elif status == "Done":
                    distance = y_distance(self.walker.get_location(), self.player.get_location())
                    if self.second_decider(distance, 5):
                        self.choice = "Cross"
                        self.walker.blend_pose(0)
                        self.path_controller_2.step()
                        self.walker.icr = ICR.GOING_TO
                        self.iss_crossed.step()
            elif self.choice == "Cross":
                if status == "Done":
                    self.walker.blend_pose(0)
                    self.path_controller_2.step()
                    self.iss_crossed.step()
            elif self.choice is None:
                if l2_distance(self.walker.get_location(), self.desc_p)<0.1:    
                    distance = y_distance(self.walker.get_location(), self.player.get_location())-2
                    #distance = l2_distance(self.walker.get_location(), self.player.get_location())
                    #print(distance)
                    #self.compute_collision_point()
                    if self.decision_trigger(distance,self.db):
                        self.choice = "Stop"
                        self.path_controller_1.set_walker_speed_relative(0.5)
                        self.look_right.relax_spine()
                        self.walker.icr = ICR.VERY_LOW
                        #self.walker.son = SON.AVERTING
                    else:
                        self.path_controller_1.set_walker_speed_relative(1.0752)
                        self.path_controller_2.cur_speed = self.path_controller_1.cur_speed
                        self.path_controller_2.speed_schedule = self.speed_schedule_cross
                        self.choice = "Cross"
                        self.walker.icr = ICR.GOING_TO
            
            relax = self.relaxer.step()
            if relax and self.choice is None:
                self.path_controller_1.speed_schedule = None
                self.path_controller_2.speed_schedule = None
                self.path_controller_1.cur_speed = self.ped_speed
                self.path_controller_2.cur_speed = self.ped_speed
                self.walker.icr = ICR.GOING_TO
                self.walker.son = SON.AVERTING
                self.choice = "Cross"
                #print("######################################################")
                #print("relax")
                #print("######################################################")

        if self.scenario[0] == "03_int":
            if self.init_char == "forcing":
                status = self.path_controller_1.step()
                self.turn_head.step()
                if self.choice == "Stop":
                    #print("Status", status, "Choice", self.choice, "Stopped", self.stopped)
                    if status == "Done" and not self.stopped:
                        self.path_controller_2.cur_speed = 0.0
                        self.path_controller_1.cur_speed = 0.0
                        self.set_walker_speed_relative(0.0)
                        self.stopped = True
                        #print("Stopped")
                    elif status=="Done":
                        distance = y_distance(self.walker.get_location(), self.player.get_location()) + 10
                        if self.second_decider(distance, 10):
                            self.walker.blend_pose(0)
                            self.path_controller_2.cur_speed = self.ped_speed
                            self.path_controller_2.step()
                            self.walker.icr = ICR.GOING_TO
                            self.choice =  "Cross"
                elif self.choice == "Cross":
                    #self.walker.blend_pose(0)
                    self.path_controller_2.step()
                else:
                    if l2_distance(self.walker.get_location(), self.flip_p)<0.1 and self.flip_choice is None:
                        distance = y_distance(self.walker.get_location(), self.player.get_location())-2

                        if self.decision_trigger(distance, self.slow_db, without_speed=True):#distance >=self.slow_db[0] and distance <= self.slow_db[1]:
                            self.flip_choice = "Error"
                            self.set_walker_speed_relative(0.7)
                            self.path_controller_1.cur_speed = self.path_controller_1.cur_speed * 0.7
                            self.turn_head.relax_spine()
                            self.walker.icr = ICR.INTERESTED
                            self.walker.son = SON.YIELDING
                        else:
                            self.flip_choice = "StandardAcc"
                            #print(self.flip_choice)
                            self.set_walker_speed_relative(1.1)
                            self.path_controller_1.cur_speed = self.path_controller_1.cur_speed * 1.1
                            self.turn_head.lean_forward(1.2)
                            self.walker.icr = ICR.PLANNING_TO
                        
                    if l2_distance(self.walker.get_location(), self.acc_p)<0.1 and self.flip_choice=="Error":
                        distance = y_distance(self.walker.get_location(), self.player.get_location())-2
                        if self.decision_trigger(distance, self.acc_db, without_speed=True):#distance >=self.acc_db[0] and distance <= self.acc_db[1]:
                            self.path_controller_1.cur_speed = self.path_controller_1.cur_speed * 1.0/0.7 * 1.2
                            self.set_walker_speed_relative(1.0/0.7 * 1.2)
                            self.turn_head.lean_forward(1)
                            self.flip_choice = "Accelerated"

                            self.walker.icr = ICR.PLANNING_TO
                            self.walker.son = SON.FORCING
                        else:
                            self.flip_choice = "Keep"
                        #print(self.flip_choice)
                    if l2_distance(self.walker.get_location(), self.desc_p)<0.1:
                        distance = y_distance(self.walker.get_location(), self.player.get_location())-2
                        #print("Desc_p")
                        if self.decision_trigger(distance, self.db):#distance >=self.db[0] and distance <= self.db[1]:
                            self.choice = "Stop"
                            self.cur_speed = self.path_controller_1.cur_speed
                            self.path_controller_1.cur_speed = self.path_controller_1.cur_speed *0.8
                            self.path_controller_1.speed_schedule = self.speed_schedule_stop
                            self.path_controller_1.set_walker_speed_relative(0.8)
                            self.turn_head.relax_spine()
                            self.walker.icr = ICR.VERY_LOW
                            #self.walker.son = SON.AVERTING
                        else:
                            self.choice = "Cross"
                            self.walker.icr = ICR.GOING_TO
                        #print(distance, self.choice)
            else:
                status = self.path_controller_1.step()
                self.turn_head.step()
                if self.choice == "Stop":
                    #print("Status", status, "Choice", self.choice, "Stopped", self.stopped)
                    if status == "Done" and not self.stopped:
                        self.path_controller_2.cur_speed = 0.0
                        self.path_controller_1.cur_speed = 0.0
                        self.set_walker_speed_relative(0.0)
                        self.stopped = True
                        #print("Stopped")
                    elif status=="Done":
                        distance = y_distance(self.walker.get_location(), self.player.get_location())+10
                        if self.second_decider(distance,20):#distance < 0:
                            self.walker.blend_pose(0)
                            self.path_controller_2.cur_speed = self.ped_speed
                            self.path_controller_2.step()
                            self.walker.icr = ICR.GOING_TO
                            self.choice = "Cross"
                elif self.choice == "Cross":
                    #self.walker.blend_pose(0)
                    self.path_controller_2.step()
                else:
                    if l2_distance(self.walker.get_location(), self.desc_p)<0.1:
                        distance = y_distance(self.walker.get_location(), self.player.get_location())-2
                        if self.decision_trigger(distance, self.db):#distance >=self.db[0] and distance <= self.db[1]:
                            self.choice = "Stop"
                            self.cur_speed = self.path_controller_1.cur_speed
                            self.path_controller_1.cur_speed = self.path_controller_1.cur_speed *0.95
                            self.path_controller_1.speed_schedule = self.speed_schedule_stop
                            self.path_controller_1.set_walker_speed_relative(0.95)
                            self.turn_head.relax_spine()
                            self.walker.icr = ICR.VERY_LOW
                            #self.walker.son = SON.AVERTING
                        else:
                            self.choice = "Cross"
                            self.turn_head.lean_forward(1.2)
                            self.walker.icr = ICR.GOING_TO
            self.iss_crossed.step()
            relax = self.relaxer.step()
            if relax and self.choice is None:
                self.path_controller_1.speed_schedule = None
                self.path_controller_1.cur_speed = self.ped_speed
                self.path_controller_2.speed_schedule = None
                self.path_controller_2.cur_speed = self.ped_speed
                self.walker.son = SON.AVERTING

        if self.scenario[0] == "01_non_int":
            self.path_controller_1.step()
            self.look_behind_right.step()
            self.reset.step()
            self.iss_crossed.step()
            self.going_to.step()

        if self.scenario[0] == "02_non_int":
            self.path_controller_1.step()
            self.look_behind_left.step()
            self.reset.step()
            self.iss_crossed.step()
            self.going_to.step()

        if self.scenario[0] == "03_non_int":
            self.path_controller_1.step()
            self.lean_forward.step()
            self.iss_crossed.step()
            #self.look_behind_left.step()
            #self.reset.step()

    def second_decider(self, distance, dec_d = None):
        if self.random:
            if self.second_choice:
                simulation_step = 0.05
                self.waiting_c += 1
                return self.waiting_c * simulation_step > self.waiting_time
            self.waiting_time = np.random.random()*2 + 1
            self.waiting_c = 0
            self.second_choice = True
        else:
            velocity = self.player.get_velocity()
            speed = (velocity.x * velocity.x + velocity.y * velocity.y) ** 0.5
            if dec_d is None:
                return distance < 0 or speed < 1 #  less than 3.6kmh
            else:
                return distance + dec_d < 0 or (speed < 1 and distance > 2.5)

    def decision_trigger(self, distance, db, without_speed=False):
        if self.random:
            choice = np.random.choice(2)
            choices = [np.random.choice(2) for i in range(10)]
            return choice==1
        else:
            velocity = self.player.get_velocity()
            speed = (velocity.x * velocity.x + velocity.y * velocity.y) ** 0.5
            return distance >=db[0] and distance <= db[1] and (speed > 1.5 or without_speed)

    def get_walker_state(self):
        loc = self.walker.get_location()
        x,y = loc.x, loc.y
        return (x,y, self.walker.icr, self.walker.son)

    def set_walker_speed_relative(self, per):
        control = self.walker.get_control()
        control.speed = per * control.speed
        self.walker.apply_control(control)

    def get_p_from_vector(self, loc1, loc2, perc):
        vec = loc2 - loc1
        return loc1 + perc * vec

    def setup_03_non_int(self, obstacles, conf, ):
        #print("03_non_int")
        spawning_distance = conf.spawning_distance
        looking_distance = conf.looking_distance
        walking_distance = conf.walking_distance
        op_reenter_distance = conf.op_reenter_distance


        base_loc = obstacles[0][1].location + carla.Location(0,-spawning_distance,0) # TODO fix 
        spawn_loc = base_loc +  carla.Location(+2,0,0)
        #print(spawn_loc)
        #print(obstacles[0][1].location)
        self.walker = self.world.try_spawn_actor(obstacles[0][0], carla.Transform(spawn_loc, obstacles[0][1].rotation))
        self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), self.ped_speed))
        self.world.tick()
        street_dist = -10.5
        offsets_1 = [(0,+walking_distance), (street_dist, + walking_distance + op_reenter_distance),(street_dist,+ walking_distance + op_reenter_distance + 50) ]
        path_1 = self._compute_plans(offsets_1, base_loc, color=carla.Color(r=255,g=0,b=0) if self.debug else None)

        self.path_controller_1 = PathController(self.world, self.walker, path_1, self.ped_speed)
        if self.debug:
            self._draw_point(spawn_loc, color=carla.Color(255,0,0))

        self.turning_point = self.get_p_from_vector(spawn_loc, path_1[0], looking_distance)
        self.lean_forward = LeanForward(self.walker,self.turning_point)
        #self.turning_point = self.get_p_from_vector( path_1[0], path_1[1], 0.05)
        self.walker.initial_son = SON.YIELDING if conf.char == "yielding" else SON.FORCING
        self.iss_crossed = InternalStateSetter(self.walker, path_1[1],icr=ICR.VERY_LOW, son=SON.AVERTING)
        

    def setup_02_non_int(self, obstacles, conf):
        #print("02_non_int")
        spawning_distance = conf.spawning_distance
        looking_distance = conf.looking_distance
        walking_distance = conf.walking_distance
        op_reenter_distance = conf.op_reenter_distance


        base_loc = obstacles[0][1].location + carla.Location(0,-spawning_distance,0) # TODO fix 
        spawn_loc = base_loc +  carla.Location(-1,0,0)
        self.walker = self.world.try_spawn_actor(obstacles[0][0], carla.Transform(spawn_loc, obstacles[0][1].rotation))
        self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), self.ped_speed))
        self.world.tick()
        street_dist = +10.5
        offsets_1 = [(0,- walking_distance), (street_dist, - walking_distance - op_reenter_distance),(street_dist,- walking_distance - op_reenter_distance - 5) ]
        path_1 = self._compute_plans(offsets_1, base_loc, color=carla.Color(r=255,g=0,b=0) if self.debug else None)

        self.path_controller_1 = PathController(self.world, self.walker, path_1, self.ped_speed)
        if self.debug:
            self._draw_point(spawn_loc, color=carla.Color(255,0,0))

        self.turning_point = self.get_p_from_vector(spawn_loc, path_1[0], looking_distance)
        self.look_behind_left = LookBehindLeftSpine(self.walker,self.turning_point, char="forcing")

        self.turning_point = self.get_p_from_vector( path_1[0], path_1[1], 0.1)
        self.reset = ResetPose(self.walker,self.turning_point)
        self.going_to = InternalStateSetter(self.walker, path_1[0],icr=ICR.GOING_TO, son=SON.FORCING)
                                            
        self.walker.initial_son = SON.YIELDING if conf.char == "yielding" else SON.FORCING
        self.iss_crossed = InternalStateSetter(self.walker, path_1[1],icr=ICR.VERY_LOW, son=SON.AVERTING)

    def setup_01_non_int(self, obstacles, conf):
        #print("01_non_int")
        spawning_distance = conf.spawning_distance
        looking_distance = conf.looking_distance
        walking_distance = conf.walking_distance
        op_reenter_distance = conf.op_reenter_distance


        base_loc = obstacles[0][1].location + carla.Location(0,-spawning_distance,0) # TODO fix 
        spawn_loc = base_loc +  carla.Location(1,0,0)
        self.walker = self.world.try_spawn_actor(obstacles[0][0], carla.Transform(spawn_loc, obstacles[0][1].rotation))
        self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), self.ped_speed))
        self.world.tick()
        street_dist = -10.5
        offsets_1 = [(0,- walking_distance), (street_dist, - walking_distance - op_reenter_distance),(street_dist,- walking_distance - op_reenter_distance - 5) ]
        path_1 = self._compute_plans(offsets_1, base_loc, color=carla.Color(r=255,g=0,b=0) if self.debug else None)

        self.path_controller_1 = PathController(self.world, self.walker, path_1, self.ped_speed)
        if self.debug:
            self._draw_point(spawn_loc, color=carla.Color(255,0,0))

        self.turning_point = self.get_p_from_vector(spawn_loc, path_1[0], looking_distance)
        self.look_behind_right = LookBehindRight(self.walker,self.turning_point, char="forcing", scenario="01_non_int")

        self.turning_point = self.get_p_from_vector( path_1[0], path_1[1], 0.1)
        self.reset = ResetPose(self.walker,self.turning_point)
        self.going_to = InternalStateSetter(self.walker, path_1[0],icr=ICR.GOING_TO, son=SON.FORCING)

        self.walker.initial_son = SON.YIELDING if conf.char == "yielding" else SON.FORCING
        self.iss_crossed = InternalStateSetter(self.walker, path_1[1],icr=ICR.VERY_LOW, son=SON.AVERTING)


    def setup_03_int(self, obstacles, conf):
        #print("03")
        self.flip_choice = None
        spawning_distance = conf.spawning_distance
        looking_distance = conf.looking_distance
        self.db = [0,20] if conf.char == "yielding" else [0,20]
        self.slow_db = [20,38]
        self.acc_db = [20,38]
        self.init_char = conf.char
        self.second_choice = False

        base_loc = obstacles[0][1].location + carla.Location(0,-spawning_distance,0)
        spawn_loc = base_loc
        self.walker = self.world.try_spawn_actor(obstacles[0][0], carla.Transform(spawn_loc, obstacles[0][1].rotation))
        self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), self.ped_speed))
        self.world.tick()

        self.walker.on_street = False
        street_x = 95
        offsets_1 = [(street_x-spawn_loc.x,0)]
        path_1 = self._compute_plans(offsets_1, base_loc, color=carla.Color(r=255,g=0,b=0) if self.debug else None)
        self.path_controller_1 = PathController(self.world, self.walker, path_1, self.ped_speed)
        if self.debug:
            self._draw_point(spawn_loc, color=carla.Color(255,0,0))

        offsets_2 = [(-21,0)]
        self.path_2 = self._compute_plans(offsets_2, base_loc, color=carla.Color(r=255,g=0,b=0) if self.debug else None)
        self.path_controller_2 = PathController(self.world, self.walker, self.path_2, self.ped_speed)
        if conf.char == "forcing":
            self.desc_p = self.get_p_from_vector(spawn_loc, path_1[0], 0.88)
        else:
            self.desc_p = self.get_p_from_vector(spawn_loc, path_1[0], 0.8)
        if self.debug:    
            self._draw_point(self.desc_p)

        self.flip_p = self.get_p_from_vector(spawn_loc, path_1[0], 0.4)

        if self.debug:
            self._draw_point(self.flip_p, carla.Color(0,0,255))

        self.acc_p = self.get_p_from_vector(spawn_loc, path_1[0], 0.55)
        if self.debug:
            self._draw_point(self.acc_p, carla.Color(0,255,0))
            self._draw_db(db=self.slow_db)
            self._draw_db(db=self.acc_db, color=carla.Color(255,0,0))
            self._draw_db(self.db, color=carla.Color(0,0,255))
        self.turn_head = TurnHeadLeftWalk(self.walker, start_pos=self.get_p_from_vector(spawn_loc, path_1[0], looking_distance), char=conf.char)
        self.relaxer = Relaxer(self.walker, self.player, self.flip_p)
        self.speed_schedule_stop = [(self.get_p_from_vector(spawn_loc, path_1[0], per) ,0.85) for per in [0.87, 0.92]]
        self.walker.initial_son = SON.YIELDING if conf.char == "yielding" else SON.FORCING
        self.iss_crossed = InternalStateSetter(self.walker, self.path_2[0],icr=ICR.VERY_LOW, son=SON.AVERTING)

    def setup_02_int(self, obstacles, conf):
        #print("02")
        spawning_distance = conf.spawning_distance
        walking_distance = conf.walking_distance
        looking_distance = conf.looking_distance
        crossing_distance = conf.crossing_distance
        op_reenter_distance = conf.op_reenter_distance
        street_delta = 3 if conf.char == "yielding" else 5
        self.db = [-1,15] if conf.char == "yielding" else [-1,10]
        #mult =  1.0 if conf.char == "yielding"  else 1.1*1.1*1.1
        self.second_choice = False

        base_loc = obstacles[0][1].location + carla.Location(0,-spawning_distance,0)
        spawn_loc = base_loc
        self.walker = self.world.try_spawn_actor(obstacles[0][0], carla.Transform(spawn_loc, obstacles[0][1].rotation))
        self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), self.ped_speed))
        self.world.tick()
        

        self.walker.on_street = False

        offsets_1 = [(0,walking_distance),(street_delta,walking_distance+crossing_distance)]
        path_1 = self._compute_plans(offsets_1, base_loc, color=carla.Color(r=255,g=0,b=0) if self.debug else None)
        self.path_controller_1 = PathController(self.world, self.walker, path_1, self.ped_speed)

        offsets_2 = [(9.5,walking_distance+crossing_distance+op_reenter_distance),(10.5,walking_distance+crossing_distance+op_reenter_distance+2),(10.5,walking_distance+crossing_distance+op_reenter_distance+10) ]
        self.path_2 = self._compute_plans(offsets_2,base_loc, color=carla.Color(r=0,g=255,b=0) if self.debug else None)
        self.path_controller_2 = PathController(self.world, self.walker, self.path_2, self.ped_speed)

        turn_p = self.get_point((0,looking_distance*walking_distance))
        self.turn_head = TurnHeadRightBehind(self.walker, turn_p)

        self.look_right = TurnHeadRightWalk(self.walker, path_1[0], conf.char)
        self.reset = ResetPose(self.walker)

        vec = path_1[1]-path_1[0]
        self.desc_p = path_1[0] + 0.9 * vec
        
        self.path_controller_1.speed_schedule = [(path_1[0] + per * vec ,0.93) for per in [0.0, 0.2, 0.4]]

        vec_2 = self.path_2[0] - path_1[1]
        self.speed_schedule_stop = [(path_1[1] + per * vec_2 ,1.355) for per in [0.0, 0.05, 0.075]]

        self.speed_schedule_cross = [(path_1[1] + per * vec_2 ,1.075) for per in [0.0, 0.05]]
        if self.debug:
            self._draw_db()
            self._draw_point(self.desc_p)
            self._draw_grid()

        #self._draw_db_circle()
        #self.world.debug.draw_point(path_1[0] + 0.2 * vec, size=0.1, color=carla.Color(r=0,g=255,b=255), life_time=0)
        self.relaxer = Relaxer(self.walker, self.player, path_1[0] + 0.2 * vec)
        self.walker.initial_son = SON.YIELDING if conf.char == "yielding" else SON.FORCING
        self.iss_crossed = InternalStateSetter(self.walker, self.path_2[0],icr=ICR.VERY_LOW, son=SON.AVERTING)

    def setup_01_int(self, obstacles, conf):
        #print("Setup 01_int")
        spawning_distance = conf.spawning_distance
        walking_distance = conf.walking_distance
        looking_distance = conf.looking_distance
        crossing_distance = conf.crossing_distance
        reenter_distance = conf.reenter_distance
        op_reenter_distance = conf.op_reenter_distance
        street_delta = 3 if conf.char == "yielding" else 5
        self.db = [-1,15] if conf.char == "yielding" else [-1,20] #TODO
        mult =  1.0 if conf.char == "yielding"  else 1.1*1.1*1.1
        
        #print(spawning_distance)
        #print(carla.Location(0,-spawning_distance,0))
        #print(obstacles[0][1].location )
        base_loc = obstacles[0][1].location + carla.Location(0,-spawning_distance,0)
        spawn_loc = base_loc #+ carla.Location(-5,0,0)
        self.walker = self.world.try_spawn_actor(obstacles[0][0], carla.Transform(spawn_loc, obstacles[0][1].rotation))
        self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), self.ped_speed))
        self.world.tick()

        offsets_1 = [(0,walking_distance),(street_delta,walking_distance+crossing_distance)]
        path_1 = self._compute_plans(offsets_1, base_loc, color=carla.Color(r=255,g=0,b=0) if self.debug else None)
        self.path_controller_1 = PathController(self.world, self.walker, path_1, self.ped_speed)

        offsets_2 = [(9.5,walking_distance+crossing_distance+op_reenter_distance), (10.5,walking_distance+crossing_distance+op_reenter_distance+2), (10.5,walking_distance+crossing_distance+op_reenter_distance+20)]
        self.path_2 = self._compute_plans(offsets_2,base_loc, color=carla.Color(r=0,g=255,b=0) if self.debug else None)
        self.path_controller_2 = PathController(self.world, self.walker, self.path_2, self.ped_speed*mult)

        reenter = walking_distance+crossing_distance+reenter_distance
        offsets_3 = [(0,reenter),(0,reenter+5)]
        path_3 = self._compute_plans(offsets_3, base_loc, color=carla.Color(r=0,g=0,b=255) if self.debug else None)
        self.path_controller_3 = PathController(self.world, self.walker, path_3, self.ped_speed)

        turn_p = self.get_point((0,looking_distance*walking_distance))
        self.turn_head = TurnHeadRightBehind(self.walker, turn_p)

        self.look_behind_right = LookBehindRight(self.walker, path_1[0], conf.char)
        self.look_behind_left = LookBehindLeft(self.walker, mult=2)
        self.reset = ResetPose(self.walker)
        
        vec = path_1[1]-path_1[0]
        self.desc_p = path_1[0] + 0.95 * vec
        #self.db = [2,10]
        if conf.char == "forcing":
            self.path_controller_1.speed_schedule = [(path_1[0] + per * vec ,1.1) for per in [0.0, 0.2, 0.4]]
        
        #self._draw_db_circle()
        if self.debug:
            self.world.debug.draw_point(path_1[0] + 0.2 * vec, size=0.1, color=carla.Color(r=0,g=255,b=255), life_time=0)
            self._draw_grid()
            self._draw_db()
        self.relaxer = Relaxer(self.walker, self.player, path_1[0] + 0.2 * vec)

        self.walker.initial_son = SON.YIELDING if conf.char == "yielding" else SON.FORCING
        self.iss_crossed = InternalStateSetter(self.walker, self.path_2[0],icr=ICR.VERY_LOW, son=SON.AVERTING)


    def _compute_plans(self, offsets, position, color=None):
        plan = []
        cur = position #self.walker.get_location()
        for offset_x, offset_y in offsets:
            loc = carla.Location(cur.x+offset_x, cur.y-offset_y,0.5)
            plan.append(loc)
            if not color is None: 
                self.world.debug.draw_point(loc, size=0.1,
                                                color=color, life_time=0)
        return plan

    def _draw_circle(self,loc,radius):
        for i in range(0,360,2):
            x = radius*math.cos(math.radians(i))
            y = radius*math.sin(math.radians(i))
            
            self.world.debug.draw_point(loc+carla.Location(-x,y,0), size=0.05,
                                                color=carla.Color(255,165,0), life_time=0)

    def _draw_db(self, db=None, color = carla.Color(0,255,0)):
        if db is None:
            db = self.db
        left = carla.Location(83,self.desc_p.y + db[0],0.5)
        right = carla.Location(103,self.desc_p.y + db[0],0.5)
        self.world.debug.draw_line(left,right,thickness=0.05, color=color)
        left = carla.Location(83,self.desc_p.y + db[1],0.5)
        right = carla.Location(103,self.desc_p.y + db[1],0.5)
        self.world.debug.draw_line(left,right,thickness=0.05, color=color)

    def compute_collision_point(self):
        walker_loc = self.walker.get_location()
        goal_loc = self.path_2[0]
        walker_dir = goal_loc-walker_loc
        car_loc = self.player.get_location()
        walker_vel = self.walker.get_velocity()
        walker_vel = walker_dir*l2_length(walker_vel)/l2_length(walker_dir)
        car_vel = self.player.get_velocity()

        self.world.debug.draw_line(walker_loc, walker_loc+2*walker_vel, thickness=0.05, color=carla.Color(255,255,255))
        self.world.debug.draw_line(car_loc, car_loc+2*car_vel, thickness=0.05, color=carla.Color(255,255,255))

    def _draw_db_circle(self):
        self._draw_circle(self.desc_p, self.db[0])
        self._draw_circle(self.desc_p, self.db[1])

    def setup_01_int_vanilla(self,spawn_point, obstacles):
        self.walker = self.world.try_spawn_actor(obstacles[0][0], obstacles[0][1])
        self.walker.apply_control(carla.WalkerControl(carla.Vector3D(0, 0, 0), self.ped_speed))
        self.world.tick()
        self._draw_grid()
        offsets_1 = [(0,),(5,10)]
        path_1 = self._compute_plans(offsets=offsets_1, color=carla.Color(r=255,g=0,b=0))
        self.path_controller_1 = PathController(self.world, self.walker, path_1, self.ped_speed)

        offsets_2 = [(15,12)]
        self.path_2 = self._compute_plans(offsets=offsets_2, color=carla.Color(r=0,g=255,b=0))
        self.path_controller_2 = PathController(self.world, self.walker, self.path_2, self.ped_speed)

        offsets_3 = [(1.5,15),(0,25)]
        path_3 = self._compute_plans(offsets=offsets_3, color=carla.Color(r=0,g=0,b=255))
        self.path_controller_3 = PathController(self.world, self.walker, path_3, self.ped_speed)

        self.look_behind_right = LookBehindRight(self.walker, path_1[0])
        self.look_behind_left = LookBehindLeft(self.walker, mult=2)
        self.reset = ResetPose(self.walker)
        turn_p = self.get_point((0,4))
        self.turn_head = TurnHeadRightBehind(self.walker, turn_p)
        vec = path_1[1]-path_1[0]
        self.desc_p = path_1[0] + 0.85 * vec
        self.db = [3,15]
        self._draw_db()
        self.world.debug.draw_point(self.desc_p, size=0.1, color=carla.Color(r=0,g=255,b=255), life_time=0)
        self.world.debug.draw_point(turn_p, size=0.1, color=carla.Color(r=0,g=255,b=255), life_time=0)
        self.choice = None


    def next_weather(self, reverse=False):
        self._weather_index += -1 if reverse else 1
        self._weather_index %= len(self._weather_presets)
        preset = self._weather_presets[self._weather_index]
        self.hud.notification('Weather: %s' % preset[1])
        self.world.set_weather(preset[0])

    def next_map_layer(self, reverse=False):
        self.current_map_layer += -1 if reverse else 1
        self.current_map_layer %= len(self.map_layer_names)
        selected = self.map_layer_names[self.current_map_layer]
        self.hud.notification('LayerMap selected: %s' % selected)

    def load_map_layer(self, unload=False):
        selected = self.map_layer_names[self.current_map_layer]
        if unload:
            self.hud.notification('Unloading map layer: %s' % selected)
            self.world.unload_map_layer(selected)
        else:
            self.hud.notification('Loading map layer: %s' % selected)
            self.world.load_map_layer(selected)

    def toggle_radar(self):
        if self.radar_sensor is None:
            self.radar_sensor = RadarSensor(self.player)
        elif self.radar_sensor.sensor is not None:
            self.radar_sensor.sensor.destroy()
            self.radar_sensor = None

    def modify_vehicle_physics(self, vehicle):
        physics_control = vehicle.get_physics_control()
        physics_control.use_sweep_wheel_collision = True
        vehicle.apply_physics_control(physics_control)

    def render(self, display):
        self.camera_manager.render(display)
        #self.semseg_sensor.render(display)
        #self.hud.render(display)

    def destroy_sensors(self):
        self.camera_manager.sensor.destroy()
        self.camera_manager.sensor = None
        self.camera_manager.index = None

        self.semseg_sensor.sensor.destroy()
        self.semseg_sensor.sensor = None
        self.semseg_sensor.index = None

    def destroy(self):
        if self.radar_sensor is not None:
            self.toggle_radar()
        sensors = [
            self.camera_manager.sensor if self.camera else None,
            self.semseg_sensor.sensor,
            self.collision_sensor.sensor,
            self.lane_invasion_sensor.sensor,
            self.gnss_sensor.sensor,
            self.imu_sensor.sensor]
        for sensor in sensors:
            if sensor is not None:
                sensor.stop()
                sensor.destroy()
        if self.player is not None:
            self.player.destroy()
        if self.walker is not None:
            self.walker.destroy()
        # if self.incoming_car is not None and self.scenario[0] in [10, 3, 7, 8]:
        if self.incoming_car is not None and self.incoming_car.is_alive:
            self.incoming_car.destroy()
        if self.scenario[0] in [11, 12]:
            if self.parked_cars is not None:
                for car in self.parked_cars:
                    car.destroy()
