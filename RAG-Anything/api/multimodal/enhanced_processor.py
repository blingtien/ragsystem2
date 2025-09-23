"""
Enhanced Multimodal Query Processor
Improved handling for incomplete content and better fallback responses
"""

import asyncio
import base64
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class EnhancedMultimodalProcessor:
    """Enhanced processor for multimodal queries with better fallback handling"""

    def __init__(self, rag_instance=None):
        self.rag = rag_instance
        self.knowledge_base_summary = None
        self._load_knowledge_base_context()

    def _load_knowledge_base_context(self):
        """Load knowledge base context for intelligent responses"""
        self.knowledge_base_summary = {
            "domain": "国家电网公司管理规章制度",
            "coverage": [
                "电网建设管理",
                "设备管理",
                "安全生产",
                "技术标准",
                "工程质量管理",
                "物资采购管理",
                "信息系统管理",
                "人力资源管理"
            ],
            "document_types": [
                "管理办法",
                "实施细则",
                "技术规范",
                "操作规程",
                "标准文件"
            ]
        }

    async def process_query_with_incomplete_content(
        self,
        query: str,
        multimodal_content: List[Dict],
        mode: str = "hybrid"
    ) -> str:
        """Process query even with incomplete multimodal content"""

        # Analyze query intent
        query_intent = self._analyze_query_intent(query)

        # Process available content
        processed_items = []
        incomplete_items = []

        for item in multimodal_content:
            if self._is_content_incomplete(item):
                incomplete_items.append(item)
            else:
                processed = await self._process_content_item(item)
                processed_items.append(processed)

        # Generate intelligent response based on available information
        if incomplete_items:
            return await self._generate_intelligent_response(
                query=query,
                query_intent=query_intent,
                processed_items=processed_items,
                incomplete_items=incomplete_items,
                mode=mode
            )
        else:
            return await self._execute_normal_query(
                query=query,
                processed_items=processed_items,
                mode=mode
            )

    def _analyze_query_intent(self, query: str) -> Dict:
        """Analyze the intent of the query"""
        intent = {
            "type": "unknown",
            "keywords": [],
            "requires_visual": False,
            "requires_knowledge_base": False
        }

        # Check for verification/judgment queries
        if any(word in query.lower() for word in ["判断", "是否", "正确", "错误", "对不对", "验证"]):
            intent["type"] = "verification"
            intent["requires_knowledge_base"] = True

        # Check for description queries
        elif any(word in query.lower() for word in ["描述", "说明", "解释", "什么", "怎么"]):
            intent["type"] = "description"

        # Check for image-related queries
        if any(word in query.lower() for word in ["图片", "图像", "照片", "画面", "视觉"]):
            intent["requires_visual"] = True

        # Extract keywords
        keywords = []
        for word in ["电网", "设备", "管理", "规定", "标准", "安全", "质量", "采购"]:
            if word in query:
                keywords.append(word)
        intent["keywords"] = keywords

        return intent

    def _is_content_incomplete(self, item: Dict) -> bool:
        """Check if content item is incomplete"""
        if item.get('type') == 'image':
            data = item.get('data', {})
            # Check for incomplete markers
            if isinstance(data, str) and 'incomplete' in data.lower():
                return True
            if isinstance(data, dict):
                content = data.get('content', '')
                if not content or content == 'incomplete':
                    return True
        return False

    async def _process_content_item(self, item: Dict) -> Dict:
        """Process a single content item"""
        processed = {
            'type': item.get('type'),
            'original': item
        }

        if item['type'] == 'image':
            # Extract any available information
            data = item.get('data', {})
            if isinstance(data, dict):
                processed['description'] = data.get('description', '')
                processed['ocr_text'] = data.get('ocr_text', '')
                processed['metadata'] = data.get('metadata', {})

        return processed

    async def _generate_intelligent_response(
        self,
        query: str,
        query_intent: Dict,
        processed_items: List[Dict],
        incomplete_items: List[Dict],
        mode: str
    ) -> str:
        """Generate an intelligent response despite incomplete content"""

        response_parts = []

        # Handle verification queries
        if query_intent["type"] == "verification":
            response_parts.append("## 判断分析\n")

            # Acknowledge the limitation
            response_parts.append("由于图像内容未能完整加载，我将基于以下信息进行分析：\n")

            # 1. Check if we have any partial information
            if processed_items:
                response_parts.append("### 可用信息：")
                for item in processed_items:
                    if item['type'] == 'image' and item.get('description'):
                        response_parts.append(f"- 图像描述：{item['description']}")
                    if item.get('ocr_text'):
                        response_parts.append(f"- 识别到的文字：{item['ocr_text'][:100]}...")

            # 2. Reference knowledge base if relevant
            if query_intent["requires_knowledge_base"] and query_intent["keywords"]:
                response_parts.append("\n### 知识库参考：")
                response_parts.append(f"根据知识库中关于{', '.join(query_intent['keywords'])}的内容：")

                # Try to get relevant information from RAG if available
                if self.rag:
                    try:
                        rag_response = await self._safe_rag_query(
                            f"查询关于{' '.join(query_intent['keywords'])}的规定和标准",
                            mode
                        )
                        if rag_response:
                            response_parts.append(rag_response[:500])
                    except Exception as e:
                        logger.error(f"RAG query failed: {e}")
                        response_parts.append("- 知识库包含相关管理规定和技术标准")
                        response_parts.append("- 具体内容需要根据完整的图像信息进行匹配")

            # 3. Provide analysis framework
            response_parts.append("\n### 判断框架：")
            response_parts.append("要准确判断图片内容的正确性，需要：")
            response_parts.append("1. **完整的视觉信息** - 图像的清晰内容和细节")
            response_parts.append("2. **具体的描述对象** - 需要验证的具体陈述或说明")
            response_parts.append("3. **知识库匹配** - 将内容与相关规定标准进行比对")

            # 4. Recommendation
            response_parts.append("\n### 建议：")
            response_parts.append("请确保：")
            response_parts.append("- 图像文件完整上传")
            response_parts.append("- 提供需要验证的具体描述")
            response_parts.append("- 说明相关的业务场景或标准要求")

        # Handle description queries
        elif query_intent["type"] == "description":
            response_parts.append("## 内容说明\n")
            if incomplete_items:
                response_parts.append("图像内容未能完整加载，以下是基于可用信息的说明：\n")

            # Provide any available information
            for item in processed_items:
                if item.get('description'):
                    response_parts.append(f"- {item['description']}")

        # Default response for other types
        else:
            response_parts.append("## 查询处理结果\n")
            response_parts.append(f"关于您的查询：\"{query}\"\n")

            if incomplete_items:
                response_parts.append("注意：部分内容（如图像）未能完整加载。\n")

            # Try RAG query with available context
            if self.rag:
                try:
                    rag_response = await self._safe_rag_query(query, mode)
                    if rag_response:
                        response_parts.append(rag_response)
                except Exception as e:
                    logger.error(f"RAG query failed: {e}")
                    response_parts.append("系统正在处理您的查询，请稍后重试。")

        return "\n".join(response_parts)

    async def _execute_normal_query(
        self,
        query: str,
        processed_items: List[Dict],
        mode: str
    ) -> str:
        """Execute normal query with complete content"""
        # Build context from processed items
        context = []
        for item in processed_items:
            if item['type'] == 'image':
                if item.get('ocr_text'):
                    context.append(f"图像文字：{item['ocr_text']}")
                if item.get('description'):
                    context.append(f"图像描述：{item['description']}")

        # Enhance query with context
        enhanced_query = query
        if context:
            enhanced_query = f"{query}\n\n相关内容：\n" + "\n".join(context)

        # Execute RAG query
        if self.rag:
            try:
                response = await self._safe_rag_query(enhanced_query, mode)
                return response
            except Exception as e:
                logger.error(f"RAG query failed: {e}")
                return self._generate_fallback_response(query, processed_items)
        else:
            return self._generate_fallback_response(query, processed_items)

    async def _safe_rag_query(self, query: str, mode: str) -> str:
        """Safely execute RAG query with error handling"""
        try:
            if hasattr(self.rag, 'aquery'):
                result = await self.rag.aquery(query, mode=mode)
                return result
            elif hasattr(self.rag, 'query'):
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.rag.query,
                    query,
                    mode
                )
                return result
        except Exception as e:
            logger.error(f"RAG query error: {e}")
            return ""

    def _generate_fallback_response(self, query: str, processed_items: List[Dict]) -> str:
        """Generate fallback response when RAG is unavailable"""
        response = f"处理查询：{query}\n\n"

        if processed_items:
            response += "已处理的内容：\n"
            for item in processed_items:
                if item['type'] == 'image':
                    response += "- 图像内容\n"
                    if item.get('description'):
                        response += f"  描述：{item['description']}\n"
        else:
            response += "未能获取到有效的内容信息。\n"

        response += "\n请确保所有内容已正确上传并重试。"
        return response