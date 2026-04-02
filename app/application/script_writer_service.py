from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import AIAdapter, OpenAICompatibleAdapter, _extract_thinking, get_ai_adapter
from app.adapters.prompt_builder import format_knowledge_flat, format_strategy_focus
from app.application.context_service import ContextService
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.schemas.app import ScriptWriterRequest

logger = logging.getLogger(__name__)

_MAX_KNOWLEDGE_ITEMS = 15
_MAX_CONTENT_CHARS = 500

# Goal key → Chinese/English label (for user message)
_GOAL_LABELS = {
    "reach_growth": ("涨粉丝", "Grow Audience"),
    "lead_generation": ("拿线索", "Get Leads"),
    "conversion": ("卖东西", "Drive Sales"),
    "education": ("传信息", "Share Knowledge"),
    "traffic_redirect": ("引流直播间", "Drive Traffic"),
    "other": ("其他", "Other"),
}


def _build_user_message(
    request: ScriptWriterRequest,
    *,
    knowledge_text: str = "",
    strategy_text: str = "",
) -> str:
    """Build the user message from request parameters + context."""
    is_en = request.language.startswith("en")
    goal_zh, goal_en = _GOAL_LABELS.get(request.goal, ("其他", "Other"))

    parts: list[str] = []

    # Required params
    if request.topic.strip():
        parts.append(f"{'topic: ' if is_en else 'topic（选题）：'}{request.topic}")
    else:
        parts.append(
            "topic: (not specified — generate based on knowledge base context and goal)"
            if is_en else
            "topic（选题）：（未指定，请根据知识库上下文和内容目标自行选题）"
        )
    parts.append(f"{'goal: ' if is_en else 'goal（内容目标）：'}{goal_en if is_en else goal_zh}")

    # Optional params
    if request.tone:
        parts.append(f"{'tone: ' if is_en else 'tone（语气风格）：'}{request.tone}")
    parts.append(f"{'word_count: ' if is_en else 'word_count（字数）：'}{request.word_count}")
    if request.cta:
        parts.append(f"{'cta: ' if is_en else 'cta（引导动作）：'}{request.cta}")
    if request.industry:
        parts.append(f"{'industry: ' if is_en else 'industry（行业）：'}{request.industry}")
    if request.reference:
        parts.append(f"\n{'reference (reference script):' if is_en else 'reference（参考文案）：'}\n{request.reference}")
    if request.extra_req:
        parts.append(f"\n{'extra_req (additional requirements):' if is_en else 'extra_req（额外要求）：'}\n{request.extra_req}")

    # Contextual info
    if strategy_text:
        parts.append(strategy_text)
    if knowledge_text:
        parts.append(knowledge_text)

    return "\n".join(parts)


class ScriptWriterService:
    def __init__(self, session: AsyncSession, ai_adapter: AIAdapter | None = None):
        self.session = session
        self.ai = ai_adapter

    async def _prepare(self, request: ScriptWriterRequest):
        """Load context, build user message, resolve adapter."""
        if not self.ai:
            self.ai = await get_ai_adapter(
                self.session, scene_key="script_writer", config_id=request.config_id
            )

        logger.info(
            "ScriptWriter: using adapter %s/%s for offer %s",
            getattr(self.ai, "provider", "?"),
            getattr(self.ai, "model", "?"),
            request.offer_id,
        )

        # Load offer context + knowledge
        ctx_service = ContextService(self.session)
        context = await ctx_service.get_offer_context(request.offer_id)

        knowledge_items = []
        for k in context.knowledge_items:
            content = (k.content_raw or "")[:_MAX_CONTENT_CHARS]
            knowledge_items.append({
                "knowledge_type": k.knowledge_type,
                "title": k.title,
                "content_raw": content,
            })

        knowledge_text = format_knowledge_flat(
            knowledge_items[:_MAX_KNOWLEDGE_ITEMS], language=request.language
        )

        # Load strategy unit context if provided
        strategy_text = ""
        if request.strategy_unit_id:
            su_repo = StrategyUnitRepository(self.session)
            unit = await su_repo.get_by_id(request.strategy_unit_id)
            if unit:
                su_dict = {
                    "name": unit.name,
                    "marketing_objective": unit.marketing_objective,
                    "audience_segment": unit.audience_segment,
                    "scenario": unit.scenario,
                    "channel": unit.channel,
                    "notes": unit.notes,
                }
                strategy_text = format_strategy_focus(su_dict, language=request.language)

        user_message = _build_user_message(
            request,
            knowledge_text=knowledge_text,
            strategy_text=strategy_text,
        )

        return self.ai, user_message, len(knowledge_items)

    async def suggest_topic(
        self,
        offer_id: str,
        strategy_unit_id: str | None = None,
        goal: str = "reach_growth",
        language: str = "zh-CN",
        config_id: str | None = None,
    ) -> str:
        """Use LLM to suggest a topic based on knowledge base + strategy context."""
        import uuid as _uuid

        if not self.ai:
            self.ai = await get_ai_adapter(
                self.session, scene_key="script_writer", config_id=config_id
            )

        is_en = language.startswith("en")
        goal_zh, goal_en = _GOAL_LABELS.get(goal, ("其他", "Other"))

        # Load knowledge
        ctx_service = ContextService(self.session)
        context = await ctx_service.get_offer_context(_uuid.UUID(offer_id))

        knowledge_items = []
        for k in context.knowledge_items:
            content = (k.content_raw or "")[:_MAX_CONTENT_CHARS]
            knowledge_items.append({
                "knowledge_type": k.knowledge_type,
                "title": k.title,
                "content_raw": content,
            })
        knowledge_text = format_knowledge_flat(
            knowledge_items[:_MAX_KNOWLEDGE_ITEMS], language=language
        )

        # Load strategy unit
        strategy_text = ""
        if strategy_unit_id:
            su_repo = StrategyUnitRepository(self.session)
            unit = await su_repo.get_by_id(_uuid.UUID(strategy_unit_id))
            if unit:
                su_dict = {
                    "name": unit.name,
                    "marketing_objective": unit.marketing_objective,
                    "audience_segment": unit.audience_segment,
                    "scenario": unit.scenario,
                    "channel": unit.channel,
                    "notes": unit.notes,
                }
                strategy_text = format_strategy_focus(su_dict, language=language)

        offer_name = context.offer.name

        if is_en:
            system = (
                "You are a creative short-video content planner. "
                "Suggest ONE specific, compelling topic for a spoken-word video script. "
                "Return ONLY the topic text, nothing else — no quotes, no explanation."
            )
            user = (
                f"Product: {offer_name}\n"
                f"Goal: {goal_en}\n"
                f"{strategy_text}\n{knowledge_text}\n\n"
                "Based on the product info, knowledge base, and goal above, "
                "suggest one specific, creative topic for a short video script. "
                "Be concrete — not generic. Output the topic only."
            )
        else:
            system = (
                "你是一位短视频内容策划专家。"
                "根据提供的信息，推荐一个具体的、有吸引力的口播选题。"
                "只返回选题文字本身，不要加引号、不要解释。"
            )
            user = (
                f"商品：{offer_name}\n"
                f"内容目标：{goal_zh}\n"
                f"{strategy_text}\n{knowledge_text}\n\n"
                "根据以上商品信息、知识库和目标，推荐一个具体的、有创意的口播选题。"
                "要具体，不要泛泛而谈。只输出选题本身。"
            )

        if isinstance(self.ai, OpenAICompatibleAdapter):
            result = await self.ai._chat(system, user, temperature=0.9, max_tokens=1024)
        else:
            # Stub fallback
            result = f"{'How ' + offer_name + ' helps you achieve more' if is_en else offer_name + '的3个你不知道的用法'}"
        _, clean = _extract_thinking(result)
        # Fallback: if LLM output was truncated inside <think> (no closing tag),
        # _extract_thinking returns the raw text as 'clean'. Strip the <think> prefix.
        if not clean.strip() and "<think>" in result and "</think>" not in result:
            clean = result.split("<think>", 1)[-1].strip()
            # Try to grab the last meaningful line as the topic
            lines = [l.strip() for l in clean.splitlines() if l.strip() and not l.strip().startswith("-")]
            clean = lines[-1] if lines else ""
        logger.info("suggest_topic: %d chars", len(clean))
        return clean.strip().strip('"').strip("'").strip("《》")

    async def generate_stream(self, request: ScriptWriterRequest) -> AsyncIterator[str]:
        """Yield SSE events: thinking, thinking_done, token, done."""
        t0 = time.monotonic()
        adapter, user_message, knowledge_count = await self._prepare(request)

        # Non-streaming fallback for StubAIAdapter
        if not isinstance(adapter, OpenAICompatibleAdapter):
            is_en = request.language.startswith("en")
            goal_zh, goal_en = _GOAL_LABELS.get(request.goal, ("其他", "Other"))
            stub_text = (
                f"[Stub] Script generation is not available without an LLM configured.\n\n"
                f"Topic: {request.topic}\nGoal: {goal_en if is_en else goal_zh}\nWord count: {request.word_count}"
                if is_en else
                f"[Stub] 未配置 LLM，无法生成文案。\n\n"
                f"选题：{request.topic}\n目标：{goal_zh}\n字数：{request.word_count}"
            )
            result = {"script": stub_text, "knowledge_count": knowledge_count}
            yield f"event: done\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
            return

        # Stream tokens
        full_output = ""
        state = "before_think"  # before_think → in_think → after_think / no_think
        content_started = False

        async for token in adapter._chat_stream(
            request.system_prompt, user_message, temperature=0.8
        ):
            full_output += token

            if state == "before_think":
                if "<think>" in full_output:
                    state = "in_think"
                    after_tag = full_output.split("<think>", 1)[1]
                    if after_tag:
                        yield f"event: thinking\ndata: {json.dumps(after_tag, ensure_ascii=False)}\n\n"
                elif len(full_output) > 20 and "<" not in full_output:
                    state = "no_think"
                    # Emit all accumulated content as tokens
                    yield f"event: token\ndata: {json.dumps(full_output, ensure_ascii=False)}\n\n"
                    content_started = True

            elif state == "in_think":
                if "</think>" in full_output:
                    before_close = token.split("</think>")[0]
                    if before_close:
                        yield f"event: thinking\ndata: {json.dumps(before_close, ensure_ascii=False)}\n\n"
                    state = "after_think"
                    yield "event: thinking_done\ndata: {}\n\n"
                    # Emit any content after </think> in this token
                    after_close = token.split("</think>")[-1] if "</think>" in token else ""
                    if after_close.strip():
                        yield f"event: token\ndata: {json.dumps(after_close, ensure_ascii=False)}\n\n"
                        content_started = True
                else:
                    yield f"event: thinking\ndata: {json.dumps(token, ensure_ascii=False)}\n\n"

            elif state in ("after_think", "no_think"):
                yield f"event: token\ndata: {json.dumps(token, ensure_ascii=False)}\n\n"

        elapsed = time.monotonic() - t0
        if state == "in_think":
            yield "event: thinking_done\ndata: {}\n\n"

        thinking, clean_text = _extract_thinking(full_output)
        logger.info(
            "ScriptWriter: generated %d chars in %.1fs, thinking=%d chars",
            len(clean_text), elapsed, len(thinking),
        )

        result = {"script": clean_text, "knowledge_count": knowledge_count}
        yield f"event: done\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
