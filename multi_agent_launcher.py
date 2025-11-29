import multiprocessing
import time
import queue # Empty hatası için
from voyager import Voyager
from global_planner import GlobalPlanner # Yazdığımız planner sınıfı
import os

def run_single_agent(agent_config, task_queue, shared_team_state):
    name = agent_config["name"]
    mc_port = agent_config["mc_port"]
    bridge_port = agent_config["bridge_port"]

    print(f"🚀 [PROCESS BAŞLATILDI] Ajan: {name} (Bridge: {bridge_port})", flush=True)

    try:
        bot = Voyager(
            mc_port=mc_port,
            bot_name=name,
            server_port=bridge_port,
            resume=True,
            ckpt_dir="ckpt",
            env_request_timeout=1200,
            openai_api_key="", # API KEY
        )

        # --- HANDSHAKE ---
        print(f"🔌 [{name}] Bağlanıyor...", flush=True)
        try:
            initial_data = bot.env.reset(options={"mode": "soft", "wait_ticks": 40})
            # bot.env.unpause()
            bot.last_events = [("observe", initial_data)]
            print(f"✅ [{name}] GİRİŞ BAŞARILI!", flush=True)
        except Exception as e:
            print(f"❌ [{name}] BAĞLANTI HATASI: {e}", flush=True)
            return
        # -----------------

        print(f"🤖 [{name}] Hazır. Döngü Başlıyor...", flush=True)

        while True:
            # 1. DURUM RAPORLA (SENSE) - ARTIK BLOKLAMA YOK!
            try:
                # Direkt sözlüğe yazıyoruz. Eski veri anında eziliyor.
                # Queue full hatası asla almazsınız.
                current_state = bot.get_agent_state()
                shared_team_state[name] = current_state
                print(f"🛰️ [{name}] STATE SNAPSHOT: {current_state.get('status')} "
                      f"| pos={current_state.get('position')} "
                      f"| last_task={current_state.get('last_task')} "
                      f"| last_success={current_state.get('last_success')}",
                      flush=True)
            except Exception as e:
                print(f"⚠️ [{name}] Rapor Hatası: {e}", flush=True)

            # 2. GÖREV DİNLE (LISTEN)
            try:
                # 2 saniye bekle
                task_data = task_queue.get(timeout=2)
                
                # ... (Geri kalan görev işleme kodu AYNI) ...
                if isinstance(task_data, dict):
                    task = task_data.get("task")
                    purpose = task_data.get("purpose", "")
                else:
                    task = task_data
                    purpose = ""

                if task and task not in ["wait", "continue"]:
                    print(f"⚡ [{name}] ÇALIŞIYOR: {task}", flush=True)
                    
                    # Çalışırken durumu güncelle (Planner anında görsün)
                    # Geçici olarak durumu değiştirip yazıyoruz
                    bot.current_status = "Working"
                    shared_team_state[name] = bot.get_agent_state()
                    
                    messages, reward, done, info = bot.rollout(
                        task=task,
                        context=f"Order: {purpose}",
                        reset_env=True
                    )
                    
                    print(
                            f"✅ [{name}] ROLLOUT BİTTİ: done={done}, "
                            f"success={info.get('success')} | info_keys={list(info.keys())}",
                            flush=True,
                        )
                    bot.current_status = "Idle"
                    # İş bitince durumu güncelle
                    shared_team_state[name] = bot.get_agent_state()
                    
                    print(f"✅ [{name}] BİTTİ.", flush=True)

            except queue.Empty:
                continue
            
            except Exception as e:
                print(f"❌ [{name}] HATA: {e}", flush=True)
                bot.current_status = "Error"
                time.sleep(5)

    except Exception as e:
        print(f"💀 [{name}] Kritik Hata: {e}", flush=True)

# --- MAIN PROCESS (PLANNER TARAFI) ---
if __name__ == '__main__':
    
    try:
        multiprocessing.set_start_method('spawn')
    except RuntimeError:
        pass
    MAIN_GOAL = "Build a house with a crafting table, furnace, and storage chests."
    # MAIN_GOAL = "Craft a diamond pickaxe"
    
    agents_data = [
        {"name": "Voyager_Miner", "mc_port": 33311, "bridge_port": 3000},
        {"name": "Voyager_Crafter", "mc_port": 33311, "bridge_port": 3001}
    ]

    # --- ÖNEMLİ DEĞİŞİKLİK: MANAGER KULLANIMI ---
    # Manager, processler arası paylaşılan veri yapıları oluşturur.
    manager = multiprocessing.Manager()
    
    # Bu sözlük tüm processler tarafından okunup yazılabilir!
    # status_queue yerine bunu kullanıyoruz.
    shared_team_state = manager.dict() 

    # Görev kuyrukları kalıyor (Çünkü emirler sırayla yapılmalı, kaybolmamalı)
    task_queues = {data["name"]: multiprocessing.Queue() for data in agents_data}

    planner = GlobalPlanner(ckpt_dir="ckpt", openai_api_key="")

    processes = []
    for data in agents_data:
        p = multiprocessing.Process(
            target=run_single_agent, 
            # status_queue yerine shared_team_state gönderiyoruz
            args=(data, task_queues[data["name"]], shared_team_state) 
        )
        processes.append(p)
        p.start()
        print(f"⏳ {data['name']} başlatıldı. Diğeri için 15 saniye bekleniyor...")
        time.sleep(15) # 5 yerine 15 veya 20 yapın ki çakışma olmasın

    print("🌍 Global Planner Devrede. Paylaşılan Hafıza İzleniyor...")
    
    while True:
        # A. DURUMLARI OKU (SENSE)
        # Queue boşaltma derdi yok! Direkt sözlüğe bakıyoruz.
        # shared_team_state.items() bize ANLIK durumu verir.
        
        current_team_snapshot = dict(shared_team_state) # Normal sözlüğe çevirip alalım
        
        # Sadece durumu (Idle/Working) ve Envanteri çekelim
        team_status_report = {}
        total_inventory = {}
        
        for name, state in current_team_snapshot.items():
            if isinstance(state, dict): # Bazen hata mesajı string gelebilir, kontrol et
                team_status_report[name] = state.get("status", "Unknown")
                
                # Envanter birleştirme
                inv = state.get("inventory", {})
                if isinstance(inv, dict):
                    for item, count in inv.items():
                        total_inventory[item] = total_inventory.get(item, 0) + count

        # DEBUG
        if team_status_report:
            print(f"\n👀 Anlık Durum: {team_status_report}")

        # B. PLANLAMA (THINK & ACT)
        # Eğer rapor veren ajan sayısı ekibe eşitse ve boşta olan varsa
        idle_agents = [n for n, s in team_status_report.items() if s == "Idle"]
        
        if idle_agents and len(team_status_report) == len(agents_data):
            print(f"🧠 Planlama... (Boştakiler: {idle_agents})")
            
            plan = planner.create_plan(
                main_goal=MAIN_GOAL,
                agents_status=team_status_report,
                shared_inventory=total_inventory
            )
            
            print(f"📜 Strateji: {plan.get('thought', '...')}")
            
            # Dağıtım
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
                        print(f"outgoing -> {agent_name}: {task}")
                        task_queues[agent_name].put({"task": task, "purpose": purpose})
        
        time.sleep(5) 

    for p in processes:
        p.terminate()