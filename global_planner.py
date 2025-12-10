import openai
import json
import os

# SkillManager'ın doğru yerden import edildiğine emin olun
from voyager.agents.skill import SkillManager
from voyager.prompts import load_prompt

class GlobalPlanner:
    def __init__(self, ckpt_dir):
        """
        Global Planner: Takımın beyni.
        """
        self.ckpt_dir = ckpt_dir
        openai.api_key = os.environ["OPENAI_API_KEY"]

        print("Global Planner: Beceri kütüphanesi yükleniyor.`..")
        
        # SkillManager'ı başlat (Voyager'ın diskteki hafızasını okur)
        self.skill_manager = SkillManager(
            ckpt_dir=self.ckpt_dir,
            resume=True,          # ÖNEMLİ: Var olan yetenekleri okumak için
            retrieval_top_k=0     # Planner retrieval yapmayacağı için 0
        )
        
        # DÜZELTME 1: Skill Index zaten metin (String) olarak gelmeli.
        # skill.py içindeki get_skill_index() fonksiyonunun metin döndürdüğünü varsayıyoruz.
        self.available_skills = self.skill_manager.get_skill_index()
        
        # Eğer get_skill_index() henüz yazılmadıysa veya boşsa kontrol ekleyelim
        if not self.available_skills:
             self.available_skills = "No skills available yet."

        print(f"Yüklenen Beceriler (Özet):\n{self.available_skills[:200]}...\n")

    def create_plan(self, main_goal, agents_status, shared_inventory):
        """
        Ana hedefi ve durumu alır, ajanlara görev dağıtır.
        """
        
        # 1. Base Prompt'u Yükle
        try:
            # voyager/prompts/global_planner.txt dosyasını okur
            base_prompt = load_prompt("global_planner")
        except FileNotFoundError:
            print("UYARI: 'global_planner.txt' bulunamadı, varsayılan prompt kullanılıyor.")
            base_prompt = "You are a Global Commander. Coordinate agents to achieve the goal."

        # DÜZELTME 2: System Prompt'u Oluşturma
        # Prompt şablonunu ve beceri listesini burada birleştiriyoruz.
        system_prompt = f"""
                        {base_prompt}
                        
                        AVAILABLE SKILLS:
                        {self.available_skills}
                        
                        INSTRUCTIONS:
                        1. Analyze the USER GOAL and SHARED INVENTORY.
                        2. Assign tasks based on dependencies.
                        3. EXPLAIN WHY you assigned that specific task to that agent.
                        4. CRITICAL: The world is PEACEFUL. Do NOT assign tasks that involve killing mobs (e.g., spiders, zombies).
                        
                        OUTPUT FORMAT (Strict JSON):
                        {{
                            "thought": "General strategy reasoning...",
                            "assignments": {{
                                "AgentName1": {{
                                    "task": "skill_name", 
                                    "purpose": "Why this task?"
                                }},
                                "AgentName2": {{
                                    "task": "wait", 
                                    "purpose": "Waiting for resources"
                                }}
                            }}
                        }}
                        """
        user_message = f"""
        USER GOAL: {main_goal}
        
        CURRENT TEAM STATUS:
        {json.dumps(agents_status)}
        
        SHARED INVENTORY:
        {json.dumps(shared_inventory)}
        """

        try:
            # GPT-5 Çağrısı
            response = openai.ChatCompletion.create(
                model="gpt-5-mini-2025-08-07", # Voyager makalesine göre planlama için GPT-5 şart [cite: 449]
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                # temperature=0.2 # Kararlılık için düşük sıcaklık [cite: 298]
            )
            
            # JSON Ayrıştırma
            content = response.choices[0].message.content
            
            # Bazen GPT-4 JSON'u markdown ```json ... ``` blokları içine koyabilir.
            # Basit bir temizlik yapalım:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].strip()

            plan = json.loads(content)
            return plan

        except Exception as e:
            print(f"Planlama Hatası: {e}")
            # Hata durumunda güvenli moda geçip herkesi beklet
            return {"assignments": {agent: "wait" for agent in agents_status.keys()}}