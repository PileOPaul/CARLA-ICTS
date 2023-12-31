import os
import yaml
import argparse
import subprocess
import time as t
from datetime import datetime
from multiprocessing import Process
from SAC.sac_controller import SAC


from SAC.sac_discrete import SacdAgent
from SAC.sac_discrete.shared_sacd import SharedSacdAgent
from benchmark.environment import GIDASBenchmark
from config import Config
from hyreal_lite.hyReal_controller import HyREAL


def run(args):
    # Careful this is the sac version of HyREAL-lite, in my experiments it did not converge
    with open(args.config) as f:
        config = yaml.load(f, Loader=yaml.SafeLoader)
    # Create environments.
    env = GIDASBenchmark(port=Config.port)
    env.world.camera = False
    agent = HyREAL(env.world, env.map, env.scene)
    env.reset_agent(agent)
    #agent = SAC(env.world, env.map, env.scene)
    #env.reset_agent(agent)
    #if Config.server:
    #    test_env = GIDASBenchmark(port=Config.port + 100, mode="VALIDATION")
    #else:
    #    test_env = None
    # Specify the directory to log.
    name = config["name"]
    config.pop("name",None)
    if args.shared:
        name = 'shared-' + name
    time = datetime.now().strftime("%Y%m%d-%H%M")
    log_dir = os.path.join(
        '_out', args.env_id, f'{name}-seed{args.seed}-{time}')
    # Create the agent.
    # path = "_out/GIDASBenchmark/shared-sacd-seed0-20220303-1356/model/3000000/"
    #config['num_steps'] = 2e6
    #Agent = SacdAgent if not args.shared else SharedSacdAgent
    Agent = SharedSacdAgent #SacdAgent if not args.shared else 
    agent = Agent(
        env=env, test_env=None, log_dir=log_dir, cuda=args.cuda,
        seed=args.seed, **config)
    print("Agent run")
    agent.run()


def run_server():
    # train environment
    port = "-carla-port={}".format(Config.port)
    if not Config.server:
        carla_p = "your path to carla"
        p = subprocess.run(['cd '+carla_p+' && ./CarlaUE4.sh your arguments' + port], shell=True)
        #cmd = 'cd '+carla_p+' && ./CarlaUE4.sh -quality-level=Low -RenderOffScreen -carla-server -benchmark -fps=50' + port
        #pro = subprocess.Popen(cmd, stdout=subprocess.PIPE, 
        #                   shell=True, preexec_fn=os.setsid)
    else:
        carla_p = "your path to carla"
        command = "unset SDL_VIDEODRIVER && ./CarlaUE4.sh  -quality-level="+ Config.qw  +" your arguments" + port # -quality-level=Low 
        p = subprocess.run(['cd '+carla_p+' && ' + command ], shell=True)
        
    return p


def run_test_server():
    # test environment
    port = "-carla-port={}".format(Config.port + 100)
    carla_p = "your path to carla"
    command = "unset SDL_VIDEODRIVER && ./CarlaUE4.sh  -quality-level="+ Config.qw  +" your arguments" + port # -quality-level=Low 
    p = subprocess.run(['cd '+carla_p+' && ' + command ], shell=True)
    return p

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--config', type=str, default='hyreal')
    parser.add_argument('--shared', action='store_true')
    parser.add_argument('--env_id', type=str, default='GIDASBenchmark')
    parser.add_argument('--cuda', action='store_true')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--port', type=int, default=2000)
    parser.add_argument('--server', action='store_true')
    parser.add_argument("--qw", type=str, default="Low")
    args = parser.parse_args()
    globals()["server"] = args.server
    Config.server = args.server
    args.config = os.path.join('your path to configs', args.config+".yaml")
    Config.port = args.port
    Config.qw = args.qw
    print('Env. port: {}'.format(Config.port))
    Config.scenarios = ["01_non_int"]
    p = Process(target=run_server)
    p.start()
    t.sleep(20)
    #if Config.server:
    #    p2 = Process(target=run_test_server)
    #    p2.start()
    #    t.sleep(20)
    
    run(args)
