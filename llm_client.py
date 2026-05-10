from __future__ import annotations

import os
from typing import Generator

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class DeepSeekClient:
    """
    DeepSeek API 客户端（兼容 OpenAI SDK）。
    支持流式输出，并可接收三个阶段的 Prompt：
    - 知识点提取
    - 维度匹配
    - 话术生成
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DeepSeek API key 不能为空。")

        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def stream_chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
    ) -> Generator[str, None, str]:
        """
        以流式方式返回模型输出文本分片。
        最终会 return 完整文本（可选使用）。
        """
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            stream=True,
        )

        full_text = ""
        for chunk in completion:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            full_text += delta
            yield delta

        return full_text

    def run_three_stage_prompts(
        self,
        *,
        source_text: str,
        prompt_knowledge_extraction: str,
        prompt_dimension_matching: str,
        prompt_script_generation: str,
        temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        """
        串行执行三个阶段的 Prompt，均采用流式输出。
        """
        stages = [
            ("知识点提取", prompt_knowledge_extraction),
            ("维度匹配", prompt_dimension_matching),
            ("话术生成", prompt_script_generation),
        ]

        for stage_name, prompt_template in stages:
            yield f"\n\n=== {stage_name} ===\n"
            user_prompt = prompt_template.format(source_text=source_text)
            for token in self.stream_chat(
                system_prompt="你是工科实训课思政元素推荐助手。",
                user_prompt=user_prompt,
                temperature=temperature,
            ):
                yield token
