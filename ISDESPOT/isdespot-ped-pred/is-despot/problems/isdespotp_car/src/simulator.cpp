#include "ped_pomdp.h"
#include "solver/despot.h"
#include "connector.h"
#include "HybridDespot.h"
#include<random>
#include<ctime>

using namespace std;

int n_sim = 5;
int n_peds = -1; // should be smaller than ModelParams::N_PED_IN

class Simulator {
public:
    typedef pair<float, Pedestrian> PedDistPair;

    int observation_size = 4 + 2*NUM_PEDESTRIANS;

    void run(connector& conn) {
        worldModel = WorldModel();

        WorldStateTracker stateTracker(worldModel);
        WorldBeliefTracker beliefTracker(worldModel, stateTracker);
        PedPomdp pomdp = PedPomdp(worldModel);

        ScenarioLowerBound *lower_bound = pomdp.CreateScenarioLowerBound("SMART");
        ScenarioUpperBound *upper_bound = pomdp.CreateScenarioUpperBound("SMART", "SMART");

        DESPOT solver = DESPOT(&pomdp, lower_bound, upper_bound);

        // for pomdp planning and print world info
        PomdpState s;
        // for tracking world state
        PomdpStateWorld world_state;

        message *m = conn.receiveMessage();


        cout << "Initial message received\n";

        n_peds =  (int) m->pedestrianPositions.size();
        cout << "Number of pedestrians: " << n_peds << std::endl;
        pomdp.num = n_peds;
        worldModel.n_peds = n_peds;

        clog << "Set number of pedestrians in scene = " << n_peds << "\n";

        world_state.car.pos = 0;
        world_state.car.vel = 0;
        world_state.num = n_peds;

        // TODO generate initial n_peds peds -> fetch data from server
        for(int i=0; i<n_peds; i++) {
            pair<float,float> &ped = m->pedestrianPositions[i];
			world_state.peds[i].goal = -1;
			world_state.peds[i].pos = COORD(ped.first,ped.second);
            world_state.peds[i].id = i;
        }

        int num_of_peds_world = n_peds;

		delete m;

		conn.sendMessage("START\n");

        clog << "Starting...\n";
        clog << "Episode Length: " << ModelParams::MAX_EPISODE_LENGTH << endl;

        for(int step=0; step < ModelParams::MAX_EPISODE_LENGTH; step++) {
            //ModelParams::numUpperBound = 0;
            //ModelParams::numLowerBound = 0;
            //ModelParams::numDefault = 0;

            cout << "====================" << "step= " << step << endl;

			// receive current observation from server
			m = conn.receiveMessage();

			if (m->terminal) {
			    cout << worldModel.inCollision(world_state) << "\n";
				cout << "Reached the goal!\n";
                conn.sendMessage("RESET\n");
				break;
			}

			if(step + 1 >= ModelParams::MAX_EPISODE_LENGTH){
				cout << "Did not reach goal. Ran out of time!\n";
                conn.sendMessage("RESET\n");
				break;
			}

			// extract current path
			Path path, pedestrian_path;
			for (auto &p : m->path) {
				path.push_back(COORD(get<0>(p),get<1>(p),get<2>(p)));
			}
            worldModel.path = path;
            this->path = path;
            if (m->pedestrianPath.size() > 0 && Globals::config.pedestrian_prediction) {
                for (auto &p : m->pedestrianPath) {
                    pedestrian_path.push_back(COORD(get<0>(p),get<1>(p),0));
                }
                solver.pedestrian_path = pedestrian_path;
            }

			// set current position to be 0 (will probably always stay that way)
			world_state.car.pos = 0;
			world_state.car.vel = m->carSpeed; // in m/s

            for(int i=0; i<n_peds; i++) {
                pair<float,float> &ped = m->pedestrianPositions[i];
                world_state.peds[i].goal = -1;
                world_state.peds[i].pos = COORD(ped.first,ped.second);
                world_state.peds[i].id = i;
            }
            // log the path of pedestrian
            clog << world_state.peds[0].pos << "\n";

            COORD &pathPos = worldModel.path[world_state.car.pos];
            if(worldModel.inCollision(world_state)){
                cout << "(" << pathPos.x << ", " << pathPos.y << ", " << pathPos.theta << ") COLLISION STATE !\n";
            }else{
                cout << "(" << pathPos.x << ", " << pathPos.y << ", " << pathPos.theta << ")NO COLLISION STATE!\n";
            }

			stateTracker.updateCar(path[world_state.car.pos]);
			stateTracker.updateVel(world_state.car.vel);

			// TODO ... update the peds in stateTracker and the pedestrians
			for (int i = 0; i<num_of_peds_world; i++) {
				Pedestrian p(world_state.peds[i].pos.x, world_state.peds[i].pos.y, world_state.peds[i].id);
				stateTracker.updatePed(p);
			}

			delete m;

            // TODO: Create "local" POMDP from world state? This one only contains the x closest pedestrians
            s.car.pos = world_state.car.pos;
            s.car.vel = world_state.car.vel;
            s.car.dist_travelled = world_state.car.dist_travelled;
            s.num = n_peds;
            std::vector<PedDistPair> sorted_peds = stateTracker.getSortedPeds();

            //update s.peds to the nearest n_peds peds
            for(int i=0; i<n_peds; i++) {
                s.peds[i] = world_state.peds[sorted_peds[i].second.id];
            }


            cout << "state=[[" << endl;
            pomdp.PrintState(s);
            cout << "]]" << endl;

            // TODO Update the belief
            beliefTracker.update();

            vector<PomdpState> samples = beliefTracker.sample(1500);
            //samples are used to construct particle belief.
            // num_scenarios is the number of scenarios sampled from particles belief to construct despot
            vector<State*> particles = pomdp.ConstructParticles(samples);
            ParticleBelief* pb = new ParticleBelief(particles, &pomdp);

            // TODO: Solve the actual POMDP
            solver.belief(pb);

            // Choose action
            int act = solver.Search().action;

            delete pb;

//            clog << "act= " << act << endl;

            /*
             * 	enum {
		     *      ACT_CUR,
		     *      ACT_ACC,
		     *      ACT_DEC
	         *  };
             *
             */

            std::string goalString;

            std::map<int, PedBelief> &beliefs = beliefTracker.peds;
            for(auto it = beliefs.begin(); it != beliefs.end(); ++it){
                PedBelief &belief = it->second;

                goalString += std::to_string(belief.id) + ",";
                for(double prob: belief.prob_goals){
                    goalString += std::to_string(prob) + ",";
                }

                if(next(it) != beliefs.end()){
                    goalString += ";";
                }else{
                    goalString += "\n";
                }
            }

            std::string message;

            switch (act){
                case PedPomdp::ACT_CUR:
                    message = "1;";
                    break;
                case PedPomdp::ACT_ACC:
                    message = "0;";
                    break;
                case PedPomdp::ACT_DEC:
                    message = "2;";
                    break;
                default:
                    cerr << "Unknown action!\n";
                    throw "Unknown action!\n";
            }

            message += goalString;

            conn.sendMessage(message.c_str());

            // cout << "Num upper bound calls: " << ModelParams::numUpperBound << "\n";
            // cout << "Num lower bound calls: " << ModelParams::numLowerBound << "\n";
            // cout << "Num default calls: " << ModelParams::numDefault << "\n";
        }

        //delete solver;

        /*
        cout << "final_state=[[" << endl;
        pomdp.PrintState(s);
        cout << "]]" << endl;
        cout << "total_reward= " << total_reward << endl;
        */
    }
    std::default_random_engine generator;

    COORD start, goal;

    Path path;
    WorldModel worldModel;
};

int main(int argc, char** argv) {
    int port = 1250;

	if (argc >= 2){
        port = atoi(argv[1]);
	}

    Globals::config.discount = 0.990;
	Globals::config.time_per_move = (1.0 / ModelParams::control_freq);
//    Globals::config.time_per_move = 0.1;
	Globals::config.search_depth = 20;

	cout << "Maximum search_depth: " << Globals::config.search_depth
	    << ", num_scenarios" << Globals::config.num_scenarios << "\n";
    cout << "Despot port: " << port << "\n";

    Seeds::root_seed(get_time_second());
    double seed = Seeds::Next();
    Random::RANDOM = Random(seed);
    cerr << "Initialized global random generator with seed " << seed << endl;

    clog << "Connect to server ...\n";

    connector conn;
    conn.establish_connection(port);
    conn.sendMessage("RESET\n");

    Simulator sim;
    for(long i=0;; i++){
        clog<<"++++++++++++++++++++++ ROUND "<<i<<" ++++++++++++++++++++"<<endl;
        sim.run(conn);
    }
}
