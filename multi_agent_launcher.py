import multiprocessing
import time
import queue
from voyager import Voyager
from global_planner import GlobalPlanner
import os


def update_proxy(st, d):
    for k, v in d.items():
        st[k] = v

def put_latest(q, payload, agent_name, drop_all=True):
    try:
        q.put_nowait(payload)
        return True
    except queue.Full:
        try:
            if drop_all:
                while True:
                    q.get_nowait()
            else:
                q.get_nowait()
        except queue.Empty:
            pass

        try:
            q.put_nowait(payload)
            print(f"🔁 [GlobalPlanner] {agent_name} queue overwrite -> latest task set.", flush=True)
            return True
        except queue.Full:
            print(f"⚠️ [GlobalPlanner] {agent_name} overwrite failed (still full).", flush=True)
            return False

def recover(bot, st, err):
    st["status"] = "Idle"
    st["has_pending_task"] = False
    st["pending_task"] = None
    st["last_success"] = False
    st["last_critique"] = f"Recovered from error: {err}"

    try:
        bot.env.reset(options={"mode": "soft", "wait_ticks": 40})
        bot.current_status = "Idle"
        try:
            cur = bot.get_agent_state()
            update_proxy(st, cur)
        except Exception:
            pass
        st["status"] = "Idle"
        return True
    except Exception:
        return False

def run_single_agent(agent_config, task_queue, shared_team_state):
    name = agent_config["name"]
    mc_port = agent_config["mc_port"]
    bridge_port = agent_config["bridge_port"]
    st = shared_team_state[name]
    print(f"🚀 [PROCESS IS STARTED] Agent: {name} (Bridge: {bridge_port})", flush=True)
    bot = None
    try:
        bot = Voyager(
            mc_port=mc_port,
            bot_name=name,
            server_port=bridge_port,
            resume=True,
            ckpt_dir="ckpt",
            env_request_timeout=120,
            openai_api_key="", # API KEY
        )
        if bot is None:
            print(f"❌ [{name}] Voyager init failed.", flush=True)
            st["status"] = "Dead"
            st["last_success"] = False
            st["last_critique"] = "Voyager init failed"
            return
        # --- HANDSHAKE ---
        print(f"🔌 [{name}] Connecting ...", flush=True)
        try:
            initial_data = bot.env.reset(options={"mode": "soft", "wait_ticks": 40})
            # bot.env.unpause()
            bot.last_events = [("observe", initial_data)]
            print(f"✅ [{name}] CONNECTION SUCCESSFUL!", flush=True)
        except Exception as e:
            print(f"❌ [{name}] CONNECTION ERROR: {e}", flush=True)
            return
        # -----------------
        bot.current_status = "Idle"
        st = shared_team_state[name]
        st["status"] = "Idle"
        print(f"🤖 [{name}] Ready. Loop Starting...", flush=True)

        while True:
            try:
                current_state = bot.get_agent_state()
                st = shared_team_state[name]   # proxy dict
                update_proxy(st, current_state)
                cur_status = getattr(bot, "current_status", st.get("status", "Unknown"))
                if st.get("has_pending_task", False) and cur_status == "Idle":
                    st["status"] = "Pending"
                else:
                    st["status"] = cur_status
                print(f"🛰️ [{name}] STATE SNAPSHOT: {st.get('status')} "
                      f"| pos={current_state.get('position')} "
                      f"| last_task={current_state.get('last_task')} "
                      f"| last_success={current_state.get('last_success')}",
                      flush=True)
                print(f"⏳ [{name}] Waiting for task...", flush=True)
                task_data = task_queue.get(timeout=2)
                print(f"📥 [{name}] New Task Received: {task_data}", flush=True)
                if isinstance(task_data, dict):
                    task = task_data.get("task")
                    purpose = task_data.get("purpose", "")
                else:
                    task = task_data
                    purpose = ""
                is_real_work = bool(task) and task not in ["wait", "continue"]
                st = shared_team_state[name]

                if is_real_work:
                    bot.current_status = "Working"
                    cur = bot.get_agent_state()
                    update_proxy(st, cur)
                    st["status"] = "Working"
                else:
                    cur = bot.get_agent_state()
                    update_proxy(st, cur)
                st["has_pending_task"] = False
                st["pending_task"] = None
                if is_real_work:
                    try:
                        messages, reward, done, info = bot.rollout(
                            task=task,
                            context=f"Order: {purpose}",
                            reset_env=True
                        )
                    except Exception as e:
                        bot.current_status = "Idle"
                        st["status"] = "Idle"
                        st["last_success"] = False
                        st["last_critique"] = f"Rollout error: {str(e)}"
                        continue
                    bot.current_status = "Idle"
                    cur = bot.get_agent_state()
                    update_proxy(st, cur)
                    st["status"] = "Idle"
            except queue.Empty:
                continue
            except Exception as e:
                # timeout / transient / rollout crash vs.
                print(f"❌ [{name}] Loop Error: {e} (Recovering...)", flush=True)
                ok = False
                if bot is not None:
                    ok = recover(bot, st, e)
                if ok:
                    continue
                else:
                    st["status"] = "Dead"
                    st["has_pending_task"] = False
                    st["pending_task"] = None
                    st["last_success"] = False
                    st["last_critique"] = f"Recover failed: {e}"
                    print(f"💀 [{name}] Recover failed -> process exiting.", flush=True)
                    break
    except Exception as e:
        st["status"] = "Dead"
        st["has_pending_task"] = False
        st["pending_task"] = None
        st["last_success"] = False
        st["last_critique"] = f"Fatal before loop: {e}"
        print(f"❌ [{name}] Fatal error: {e}", flush=True)
        return

if __name__ == '__main__':
    
    try:
        multiprocessing.set_start_method('spawn')
    except RuntimeError:
        pass    
    # MAIN_GOAL = "Build a house with a crafting table, furnace."
    MAIN_GOAL = "Build a iron pickaxe"
    # MAIN_GOAL = "Craft a diamond pickaxe"
    
    agents_data = [
        {"name": "Voyager_Miner", "mc_port": 43781, "bridge_port": 3000},
        {"name": "Voyager_Crafter", "mc_port": 43781, "bridge_port": 3001}
    ]
    manager = multiprocessing.Manager()
    shared_team_state = manager.dict() 

    for data in agents_data:
        shared_team_state[data["name"]] = manager.dict({
            "status": "Unknown",
            "has_pending_task": False,
            "pending_task": None,
        })
    task_queues = {data["name"]: multiprocessing.Queue(maxsize=1) for data in agents_data}
    planner = GlobalPlanner(ckpt_dir="ckpt")
    processes = []
    for data in agents_data:
        p = multiprocessing.Process(
            target=run_single_agent, 
            args=(data, task_queues[data["name"]], shared_team_state) 
        )
        processes.append(p)
        p.start()
        print(f"⏳[GlobalPlanner] {data['name']} started. Waiting 15 seconds for others...")
        time.sleep(15)

    print("🌍 [GlobalPlanner] Global Planner is active. Monitoring Shared Memory...")

    while True:
        current_team_snapshot = {k: dict(v) for k, v in shared_team_state.items()}
        team_status_report = {}
        total_inventory = {}
        
        for name, state in current_team_snapshot.items():
            if isinstance(state, dict): # Sometimes error messages can come as strings, check
                print(f"👀[GlobalPlanner] [{name}] Fetching Current Status...", flush=True)
                team_status_report[name] = state.get("status", "Unknown")
                print(f"👀[GlobalPlanner] [{name}] Current Status Retrieved: {state.get('status')}", flush=True)

                # Inventory merging
                inv = state.get("inventory", {})
                if isinstance(inv, dict):
                    for item, count in inv.items():
                        total_inventory[item] = total_inventory.get(item, 0) + count

        # DEBUG
        if team_status_report:
            print(f"\n👀 Current Status: {team_status_report}")

        # B. PLANLAMA (THINK & ACT)
        idle_agents = [n for n, s in team_status_report.items() if s == "Idle"]
        
        if idle_agents and len(team_status_report) == len(agents_data):
            print(f"🧠[GlobalPlanner] Planning... (Idle: {idle_agents})")
            plan = planner.create_plan(
                main_goal=MAIN_GOAL,
                agents_status=team_status_report,
                shared_inventory=total_inventory
            )
            print(f"📜 [GlobalPlanner] Strategy: {plan.get('thought', '...')}")
            assignments = plan.get("assignments", {})
            for agent_name, assignment_data in assignments.items():
                if isinstance(assignment_data, dict):
                    task = assignment_data.get("task")
                    purpose = assignment_data.get("purpose", "")
                else:
                    task = assignment_data
                    purpose = ""

                if task and task not in ["wait", "continue"]:
                    if agent_name in task_queues:
                        print(f"[GlobalPlanner] outgoing -> {agent_name}: {task}")
                        if team_status_report.get(agent_name) != "Idle":
                            continue
                        agent_state = current_team_snapshot.get(agent_name, {})
                        if agent_state.get("status") != "Idle":
                            continue
                        if agent_state.get("has_pending_task", False):
                            continue

                        success = put_latest(task_queues[agent_name], {"task": task, "purpose": purpose}, agent_name, drop_all=False)
                        if success:
                            st = shared_team_state[agent_name]
                            st["has_pending_task"] = True
                            st["pending_task"] = task
                            if st.get("status") in ["Idle", "Unknown"]:
                                st["status"] = "Pending"
        time.sleep(5)
        print("\n-----------------------------\n")
