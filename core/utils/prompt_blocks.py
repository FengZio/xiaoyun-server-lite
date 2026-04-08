from dataclasses import dataclass
import re
from typing import Dict, Tuple


@dataclass
class PromptBlock:
    text: str = ""
    usage_hint: str = ""
    enabled: bool = True

    def render(self) -> str:
        if not self.enabled or not self.text:
            return ""
        if self.usage_hint:
            return f"{self.usage_hint}\n{self.text}".strip()
        return self.text.strip()


@dataclass
class PromptBlocks:
    policy_block: PromptBlock
    persona_block: PromptBlock
    state_block: PromptBlock
    memory_block: PromptBlock


class PromptAssembler:
    @staticmethod
    def _inject_block(
        prompt: str, tag: str, block_text: str, marker: str = "[交互指南]"
    ) -> Tuple[str, str]:
        if not block_text:
            return prompt, "skipped"

        block = f"<{tag}>\n{block_text}\n</{tag}>"
        pattern = rf"<{tag}>.*?</{tag}>"
        if re.search(pattern, prompt, flags=re.DOTALL):
            prompt = re.sub(pattern, block, prompt, flags=re.DOTALL)
            return prompt, "replaced"

        insert_pos = prompt.find(marker)
        if insert_pos != -1:
            after_marker = insert_pos + len(marker)
            remainder = prompt[after_marker:]
            next_header = re.search(r"\n\[.+?\]", remainder)
            if next_header:
                insert_at = after_marker + next_header.start()
            else:
                insert_at = len(prompt)
            prompt = prompt[:insert_at] + "\n" + block + "\n" + prompt[insert_at:]
            return prompt, "inserted"

        prompt = prompt.rstrip() + "\n" + block + "\n"
        return prompt, "appended"

    @staticmethod
    def render(base_prompt: str, blocks: PromptBlocks) -> Tuple[str, Dict[str, str]]:
        prompt = base_prompt or ""
        stats: Dict[str, str] = {}

        prompt, stats["policy"] = PromptAssembler._inject_block(
            prompt, "policy", blocks.policy_block.render()
        )
        prompt, stats["persona"] = PromptAssembler._inject_block(
            prompt, "persona", blocks.persona_block.render()
        )
        prompt, stats["state"] = PromptAssembler._inject_block(
            prompt, "state", blocks.state_block.render()
        )
        prompt, stats["memory"] = PromptAssembler._inject_block(
            prompt, "memory", blocks.memory_block.render()
        )

        return prompt, stats
