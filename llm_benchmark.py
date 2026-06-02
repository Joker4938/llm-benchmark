import asyncio
import time
import numpy as np
from openai import AsyncOpenAI
import logging
import argparse
import json
import random

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SHORT_PROMPTS = [
    "用简单的话解释什么是人工智能。",
    "气候变化的主要成因是什么？",
    "描述植物是如何进行光合作用的。",
    "人类的免疫系统是如何运作的？",
    "第二次世界大战爆发的主要原因有哪些？",
    "用通俗的语言解释相对论。",
    "有效领导的关键原则有哪些？",
    "区块链技术是如何工作的？",
    "关于宇宙起源的主要理论有哪些？",
    "解释水循环以及它对地球生命的重要性。",
    "资本主义与社会主义有何主要区别？",
    "人类大脑是如何处理并存储记忆的？",
    "太空探索面临的主要挑战是什么？",
    "解释经济学中的供需原理。",
    "一个汉字具有左右结构，左边是木，右边是乞。这个字是什么？只需回答这个字即可。"
    "9.11 和 9.9 哪个大？",
]

LONG_PROMPT_PAIRS = [
    {
        "prompt": "用简单的话解释什么是人工智能。",
        "context": "人工智能 (AI) 是计算机科学中一个快速发展的领域，旨在创造能够执行通常需要人类智能的任务的智能机器。这些任务包括视觉感知、语音识别、决策和语言翻译。AI 系统被设计为能够从经验中学习，适应新的输入，并执行类似人类的任务。AI 涉及多个子领域，如机器学习、神经网络和深度学习，这些领域在自动驾驶汽车、虚拟助手和推荐系统等方面取得了显著进展。"
    },
    {
        "prompt": "气候变化的主要成因是什么？",
        "context": "气候变化是一个复杂的全球现象，主要由人类活动引起，这些活动将温室气体释放到大气中。化石燃料的燃烧、森林砍伐、工业过程和农业是导致二氧化碳和其他温室气体浓度增加的主要因素。这些气体在地球周围形成一个“毯子”，导致地球以史无前例的速度变暖。温度模式的变化导致更多频繁和严重的天气事件、海平面上升以及全球生态系统的破坏。"
    },
    {
        "prompt": "描述植物是如何进行光合作用的。",
        "context": "光合作用是一个基本的生物过程，使植物能够将光能转化为化学能。这个过程发生在植物细胞的叶绿体中，特别是在称为类囊体的结构中。叶绿素是植物中呈现绿色的色素，对捕捉光能至关重要。在光合作用中，植物通过称为气孔的小孔吸收空气中的二氧化碳，并通过根部从土壤中吸收水分。利用光能，它们将这些成分结合起来产生葡萄糖和氧气。这个过程不仅为植物提供能量，还释放出氧气，作为地球上大多数生命的副产物。"
    },
    {
        "prompt": "人类的免疫系统是如何运作的？",
        "context": "人类免疫系统是由细胞、组织和器官组成的复杂网络，它们共同作用以保护身体免受有害病原体的侵害。免疫系统由两大部分组成：先天免疫系统，提供快速、非特异性的响应；以及适应性免疫系统，发展针对特定病原体的定向防御。关键成分包括白细胞（如中性粒细胞、巨噬细胞和淋巴细胞）、抗体和补体系统。免疫系统能够区分自身细胞和外来入侵者，使其能够在锁定威胁的同时尽量减少对健康组织的损害。"
    },
    {
        "prompt": "第二次世界大战的主要原因是什么？",
        "context": "第二次世界大战从1939年持续到1945年，是人类历史上最致命的冲突之一。其起源可以追溯到多个复杂因素。第一次世界大战结束后的凡尔赛条约的苛刻条款使德国经济遭到破坏并心怀不满，为法西斯主义和纳粹党在阿道夫·希特勒领导下的崛起铺平了道路。纳粹德国、法西斯意大利和日本帝国的侵略性扩张政策，加上西方列强的绥靖政策，使这些政权能够不受阻碍地获得领土。战争在欧洲的直接导火索是德国于1939年9月入侵波兰，而1941年袭击珍珠港则将美国卷入冲突。"
    },
    {
        "prompt": "用通俗的语言解释相对论。",
        "context": "阿尔伯特·爱因斯坦在20世纪初期发展了相对论理论，彻底改变了我们对空间、时间和引力的理解。它由两个部分组成：狭义相对论和广义相对论。狭义相对论于1905年提出，处理以非常高速度运动的物体。它提出光速对所有观察者都是恒定的，时间和空间不是绝对的，而是相对于观察者的运动。这导致了时间膨胀和长度收缩等现象。广义相对论于1915年发布，将这些思想扩展到包括引力。爱因斯坦提出，大质量物体会弯曲时空的结构，这种弯曲就是我们体验到的引力。这些理论得到了实验证据的一贯支持，并在GPS卫星等技术中有实际应用。"
    },
    {
        "prompt": "有效领导的关键原则有哪些？",
        "context": "有效的领导对于引导组织、团队和个人实现目标至关重要。虽然领导风格可能有所不同，但几个关键原则被广泛认为是成功的必要条件。这些包括清晰的沟通，确保所有人理解愿景和期望；诚信，建立信任和尊重；适应性，使领导者能够在变化的环境中导航；同理心，促进强大的人际关系并理解团队动态；决策能力，能够做出及时和明智的选择；愿景，提供方向和灵感；以及赋予他人权力的能力，鼓励团队内部的成长和创新。有效的领导者还表现出对自己和团队的行动负责，并不断寻求个人成长和学习机会。"
    },
    {
        "prompt": "区块链技术是如何工作的？",
        "context": "区块链是一种去中心化的分布式账本技术，是比特币等加密货币的基础，但其潜在应用远远超出数字货币。区块链的核心是一个区块链，每个区块包含一系列交易。每个区块通过加密哈希与前一个区块链接，创建一个不可更改的记录。区块链的关键创新在于其能够在一个去中心化的网络中实现共识，而无需信任任何单一实体。这通常通过工作量证明或权益证明等共识机制来实现。当发生新交易时，它会广播到一个计算机（节点）网络进行验证。一旦验证，交易就与其他交易组合创建一个新块，然后添加到链中。这个过程确保了透明度、安全性和防篡改性，使区块链适用于金融以外的各种应用，包括供应链管理、投票系统和数字身份验证。"
    },
    {
        "prompt": "关于宇宙起源的主要理论有哪些？",
        "context": "宇宙起源一直是科学和哲学激烈探讨的主题。目前，最广泛接受的科学理论是大爆炸模型，该模型提出宇宙始于大约138亿年前一个无限致密和炽热的奇点，并从那时起一直在膨胀和冷却。这个理论得到的观测证据包括宇宙微波背景辐射和宇宙中轻元素的丰度。然而，关于大爆炸之前发生了什么以及它的起因仍然存在疑问。其他理论包括稳态理论，认为宇宙一直存在并在扩展时不断创造新物质，尽管由于缺乏支持证据，这一理论已不再受欢迎。更具推测性的想法包括循环宇宙的概念，其中大爆炸和大挤压在一个无限循环中发生，以及多元宇宙的想法，我们的宇宙只是许多存在的宇宙之一。"
    },
    {
        "prompt": "解释水循环以及它对地球生命的重要性。",
        "context": "水循环，也称为水文循环，是水在地球和大气中连续运动的过程。它是一个复杂的系统，涉及蒸发、蒸腾、凝结、降水和径流的过程。水由于太阳能从地球表面蒸发，主要来自海洋、湖泊和河流。植物也通过蒸腾释放水蒸气。当水蒸气在大气中上升时，它冷却并凝结形成云。最终，它以雨、雪或冰雹的形式降回地球。部分水作为地表径流流过陆地，返回水体，而部分水渗入地下，补充地下水储备。这个循环对地球生命至关重要，因为它将水分布到全球各地，通过侵蚀和沉积塑造景观，调节全球温度，并提供所有生物体所需的淡水。理解和保护水循环对于管理水资源和解决气候变化和水资源匮乏等环境挑战至关重要。"
    },
    {
        "prompt": "资本主义与社会主义有何主要区别？",
        "context": "资本主义和社会主义是两种对比鲜明的经济和政治体系，塑造了现代历史的大部分。资本主义的特点是生产资料的私人所有制，个人或公司拥有企业和财产。它基于自由市场竞争的原则，价格由供需决定。利润是资本主义体系中的关键动力，政府干预通常有限。相反，社会主义倡导集体或政府所有和管理生产资料和商品的分配。它旨在通过减少阶级差异并根据需要而不是支付能力分配资源来创造一个更公平的社会。在社会主义体系中，政府在经济规划和社会服务提供方面起着更大的作用。虽然纯粹形式的任何一种系统都很少见，但许多国家采用了混合经济，将资本主义和社会主义的元素结合在一起，程度不一。"
    },
    {
        "prompt": "人类大脑是如何处理并存储记忆的？",
        "context": "人类大脑处理和存储记忆的能力是一个复杂而迷人的过程，涉及各种区域和神经网络。当我们经历某件事情时，感官信息首先在相关的皮层区域（如视觉皮层用于视觉，听觉皮层用于听觉）进行处理。然后，这些信息在海马体中整合，海马体是一个海马形的结构，对形成新记忆至关重要。海马体有助于将体验的不同方面结合成一个连贯的记忆，并在将短期记忆转化为长期记忆中发挥关键作用。长期记忆被认为是通过皮层广泛区域的神经元之间突触连接的变化存储的。这个过程称为巩固，可能需要数天甚至数年。不同类型的记忆（如情景记忆、语义记忆、程序性记忆）涉及不同的大脑区域和过程。记忆的检索涉及重新激活这些神经模式，这解释了为什么记忆会受到我们当前状态和环境的影响。理解这些过程对于解决记忆相关疾病和开发潜在的治疗方法至关重要。"
    },
    {
        "prompt": "太空探索面临的主要挑战是什么？",
        "context": "太空探索虽然提供了巨大的科学发现和技术进步的潜力，但也面临许多挑战。一个主要障碍是太空本身的恶劣环境。太空的真空、极端温度和有害辐射对人类宇航员和敏感设备构成重大风险。长期暴露在微重力环境中会导致宇航员的健康问题，包括肌肉萎缩和骨密度下降。后勤挑战也很大：太空旅行中涉及的巨大距离需要先进的推进系统和仔细的资源管理。将有效载荷发射到轨道仍然非常昂贵，限制了任务的范围和频率。通信延迟对深空任务来说越来越成问题，需要航天器和探测器具有高度的自主性。此外，地球轨道上的太空碎片对卫星和航天器构成日益严重的威胁。随着我们展望在月球或火星上建立基地等长期目标，我们面临着在创建可持续栖息地以及在长期任务中对船员成员的心理影响进行管理方面的新挑战。尽管存在这些障碍，持续的研究和技术创新继续推动太空探索的可能性边界。"
    },
    {
        "prompt": "解释经济学中的供需原理。",
        "context": "供需是经济学中的一个基本概念，描述了市场中商品或服务的价格和数量是如何通过买卖双方的互动决定的。需求定律指出，在其他条件相同的情况下，随着产品价格的上涨，消费者需求的数量减少。这通常由向下倾斜的需求曲线表示。相反，供给定律指出，随着产品价格的上涨，生产者愿意供给的数量增加，由向上倾斜的供给曲线表示。这两条曲线交叉的点称为均衡点，决定了市场价格和数量。这个模型有助于解释价格如何随着供需变化而波动。例如，如果需求增加而供给保持不变，价格将上涨。如果供给增加而需求保持不变，价格将下降。理解供需对于分析市场行为、预测价格变化和制定经济政策至关重要。"
    },
    {
        "prompt": "民主政府的主要特征是什么？",
        "context": "民主政府是一种以人民统治原则为基础的治理系统。虽然民主可以采取多种形式，但它们通常共享几个关键特征。首先是自由和公正的选举概念，公民有权在定期的间隔投票选举他们的代表。这与政治多元化原则密切相关，允许多个政党和观点竞争权力。保护个人权利和公民自由，如言论自由、新闻自由和集会自由，是民主的另一个关键方面。权力分立通常被实施以防止权力集中，通常将政府分为行政、立法和司法部门，相互提供制衡。法治，确保所有公民，包括掌权者，均受法律的约束，是民主治理的基础。政府运作的透明度和问责制，通常通过自由媒体和积极的公民社会来促进，帮助维护民主原则。此外，许多民主强调少数群体权利的保护和多数统治与少数权利的概念，旨在平衡多数的意愿与所有公民的基本权利。"
    },
    {
        "prompt": "疫苗如何预防疾病？",
        "context": "疫苗是预防传染病的最有效工具之一，通过利用身体自身的免疫系统起作用。当病原体如病毒或细菌进入体内时，免疫系统通过产生特定于该病原体的抗体来响应。这些抗体有助于中和或摧毁入侵者。疫苗通过将病原体的无害形式引入体内——无论是减弱的、灭活的还是仅仅是部分病原体——模仿这一自然过程。这刺激免疫系统产生针对该病原体的抗体和记忆细胞，而不会引起实际疾病。如果接种疫苗的人后来遇到真正的病原体，他们的免疫系统可以迅速识别并迅速有效地响应，通常完全防止疾病或减少其严重性。有些疫苗需要多次接种或定期加强来维持免疫力。群体免疫的概念在疫苗接种策略中也很重要：当大部分人口接种疫苗时，病原体的传播变得困难，从而间接保护那些无法接种疫苗的人。疫苗技术的进步，如 mRNA 疫苗，正在扩展我们快速开发针对新威胁的疫苗的能力。"
    },
    {
        "prompt": "人类进化的主要理论是什么？",
        "context": "人类进化是研究我们物种智人及其祖先的生物和文化发展的学科。解释人类进化的主要科学理论基于达尔文的自然选择进化理论，已适应并包含现代基因学理解。该理论提出人类从早期灵长类物种进化而来，历时数百万年。关键思想包括共同祖先的概念，暗示人类与其他灵长类动物，特别是类人猿共享共同祖先。“走出非洲”理论指出，现代人类起源于非洲，然后迁徙到世界其他地方。化石证据揭示了一系列中间物种，如南方古猿、能人和直立人，显示了在脑容量、双足行走和工具使用等特征上的逐渐变化。最近的发现和基因研究使这一图景复杂化，表明不同人类物种（如智人和尼安德特人）之间的杂交以及可能的多次非洲迁徙。古生物学、遗传学和考古学的持续研究不断完善我们对人类进化的理解，常常挑战先前的假设并揭示我们物种复杂的历史。"
    },
    {
        "prompt": "描述板块构造过程及其对地球的影响。",
        "context": "板块构造是地质学中的一个基本理论，用来解释地球岩石圈的大规模运动。该理论提出地球的外层被分为几个大的刚性板块，这些板块相互移动。这些板块漂浮在半流体的软流圈上，并由地幔中的对流驱动。板块边界分为三种类型：离散边界，板块在此分开并形成新的地壳；汇聚边界，板块在此碰撞，导致俯冲或造山；和转换边界，板块在此水平滑动。板块构造过程对地球表面和内部结构有深远的影响。它是山脉、海洋盆地和岛弧形成的原因。它还在岩石循环、火山活动和地震发生中起着至关重要的作用。从地质时期来看，板块构造影响了气候模式、洋流以及全球动植物的分布。理解板块构造对预测地质灾害、解释自然资源分布以及理解地球的长期地质历史至关重要。"
    },
    {
        "prompt": "生物多样性丧失的主要原因是什么？",
        "context": "生物多样性丧失，即地球上生命形式的多样性下降，是一个具有深远影响的关键环境问题，影响生态系统和人类福祉。几个相互关联的因素导致了这种丧失。栖息地破坏和碎片化，通常由人类活动如森林砍伐、城市化和农业扩张引起，是主要驱动因素。气候变化日益被认为是一个主要威胁，改变生态系统的速度超过许多物种的适应能力。自然资源的过度开发，包括过度捕捞和偷猎，直接减少了许多物种的种群数量。污染，包括化学径流、塑料废物和空气污染，破坏栖息地并危害野生动物。入侵物种的引入，通常是由于人类活动，可能破坏当地生态系统并与本地物种竞争。此外，疾病的传播，有时由于气候变化和栖息地压力而加剧，可能对某些物种的种群造成毁灭性影响。这些因素常常相互作用并加剧彼此的影响，加速了生物多样性丧失的速度。解决这一危机需要全面的保护策略、可持续的资源管理和全球合作以减轻人类对自然生态系统的影响。"
    },
]

async def process_stream(stream):
    first_token_time = None
    total_tokens = 0
    usage_tokens = None
    async for chunk in stream:
        if not chunk.choices:
            # Usage-only chunk (when stream_options includes usage)
            if hasattr(chunk, 'usage') and chunk.usage:
                usage_tokens = getattr(chunk.usage, 'completion_tokens', None)
            continue
        delta = chunk.choices[0].delta
        # Check for actual content (skip empty strings from init chunks)
        has_content = bool(delta.content and delta.content.strip())
        # Different APIs use different field names: DeepSeek uses reasoning_content, Qwen uses reasoning
        if not has_content:
            for field in ('reasoning_content', 'reasoning'):
                val = getattr(delta, field, None)
                if val and val.strip():
                    has_content = True
                    break
        if has_content:
            if first_token_time is None:
                first_token_time = time.time()
            total_tokens += 1
        if chunk.choices[0].finish_reason is not None:
            break
    # Prefer API-reported token count over chunk counting
    final_tokens = usage_tokens if usage_tokens is not None else total_tokens
    return first_token_time, final_tokens

async def make_request(client, model, output_tokens, request_timeout, use_long_context):
    start_time = time.time()
    if use_long_context:
        prompt_pair = random.choice(LONG_PROMPT_PAIRS)
        content = prompt_pair["context"] + "\n\n" + prompt_pair["prompt"]
    else:
        content = random.choice(SHORT_PROMPTS)

    try:
        async def _do_req():
            stream = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": content}
                ],
                max_tokens=output_tokens,
                stream=True,
                stream_options={"include_usage": True},
                timeout=request_timeout
            )
            return await process_stream(stream)
            
        first_token_time, total_tokens = await asyncio.wait_for(_do_req(), timeout=request_timeout)
        
        end_time = time.time()
        elapsed_time = end_time - start_time
        ttft = first_token_time - start_time if first_token_time else None
        tokens_per_second = total_tokens / elapsed_time if elapsed_time > 0 else 0
        return total_tokens, elapsed_time, tokens_per_second, ttft

    except asyncio.TimeoutError:
        logging.warning(f"Request timed out after {request_timeout} seconds")
        return None
    except Exception as e:
        logging.error(f"Error during request: {str(e)}")
        return None

async def worker(client, semaphore, queue, results, model, output_tokens, request_timeout, use_long_context, completed_counter=None, progress_callback=None, total_requests=0):
    while True:
        async with semaphore:
            task_id = await queue.get()
            if task_id is None:
                queue.task_done()
                break
            logging.info(f"Starting request {task_id}")
            result = await make_request(client, model, output_tokens, request_timeout, use_long_context)
            if result:
                results.append(result)
            else:
                logging.warning(f"Request {task_id} failed")
            queue.task_done()
            logging.info(f"Finished request {task_id}")
            if completed_counter is not None:
                completed_counter[0] += 1
                if progress_callback:
                    try:
                        progress_callback(completed_counter[0], total_requests)
                    except Exception as e:
                        logging.error(f"Error in progress callback: {str(e)}")

import httpx

def calculate_percentile(values, percentile, reverse=False):
    if not values:
        return None
    if reverse:
        return float(np.percentile(values, 100 - percentile))
    return float(np.percentile(values, percentile))

async def run_benchmark(num_requests, concurrency, request_timeout, output_tokens, llm_url, api_key, model, use_long_context, progress_callback=None):
    # Configure HTTP client to handle high concurrency without connection pooling bottlenecks
    limits = httpx.Limits(max_connections=concurrency + 100, max_keepalive_connections=concurrency + 100)
    http_client = httpx.AsyncClient(limits=limits)
    
    client = AsyncOpenAI(base_url=llm_url, api_key=api_key, http_client=http_client)
    semaphore = asyncio.Semaphore(concurrency)
    queue = asyncio.Queue()
    results = []
    completed_counter = [0]

    # Add tasks to the queue
    for i in range(num_requests):
        await queue.put(i)
    
    # Add sentinel values to stop workers
    for _ in range(concurrency):
        await queue.put(None)

    # Create worker tasks
    workers = [asyncio.create_task(worker(
        client, semaphore, queue, results, model, output_tokens, request_timeout, use_long_context,
        completed_counter=completed_counter, progress_callback=progress_callback, total_requests=num_requests
    )) for _ in range(concurrency)]

    start_time = time.time()
    
    # Wait for all tasks to complete
    await queue.join()
    await asyncio.gather(*workers)

    end_time = time.time()

    # Calculate metrics
    total_elapsed_time = end_time - start_time
    total_tokens = sum(tokens for tokens, _, _, _ in results if tokens is not None)
    latencies = [elapsed_time for _, elapsed_time, _, _ in results if elapsed_time is not None]
    tokens_per_second_list = [tps for _, _, tps, _ in results if tps is not None]
    ttft_list = [ttft for _, _, _, ttft in results if ttft is not None]

    successful_requests = len(results)
    requests_per_second = successful_requests / total_elapsed_time if total_elapsed_time > 0 else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    avg_tokens_per_second = sum(tokens_per_second_list) / len(tokens_per_second_list) if tokens_per_second_list else 0
    avg_ttft = sum(ttft_list) / len(ttft_list) if ttft_list else 0
    
    # Calculate percentiles
    percentiles = [50, 95, 99]
    latency_percentiles = [calculate_percentile(latencies, p) for p in percentiles]
    tps_percentiles = [calculate_percentile(tokens_per_second_list, p, reverse=True) for p in percentiles]
    ttft_percentiles = [calculate_percentile(ttft_list, p) for p in percentiles]
    
    return {
        "total_requests": num_requests,
        "successful_requests": successful_requests,
        "concurrency": concurrency,
        "request_timeout": request_timeout,
        "max_output_tokens": output_tokens,
        "use_long_context": use_long_context,
        "model": model,  # 添加模型信息到结果中
        "total_time": total_elapsed_time,
        "requests_per_second": requests_per_second,
        "total_output_tokens": total_tokens,
        "latency": {
            "average": avg_latency,
            "p50": latency_percentiles[0],
            "p95": latency_percentiles[1],
            "p99": latency_percentiles[2]
        },
        "tokens_per_second": {
            "average": avg_tokens_per_second,
            "p50": tps_percentiles[0],
            "p95": tps_percentiles[1],
            "p99": tps_percentiles[2]
        },
        "time_to_first_token": {
            "average": avg_ttft,
            "p50": ttft_percentiles[0],
            "p95": ttft_percentiles[1],
            "p99": ttft_percentiles[2]
        }
    }

def print_results(results, output_format="both"):
    """
    打印测试结果
    :param results: 测试结果字典
    :param output_format: 输出格式 ('json', 'line', 'both')
    """
    if output_format in ['json', 'both']:
        print(json.dumps(results, indent=2))
    
    if output_format in ['line', 'both']:
        def fmt(val, decimals=3):
            return f"{val:.{decimals}f}" if val is not None else "N/A"

        print("\n基本信息:")
        print(f"总请求数: {results['total_requests']} 个")
        print(f"成功请求数: {results['successful_requests']} 个")
        print(f"并发数: {results['concurrency']} 个")
        print(f"请求超时: {results['request_timeout']} 秒")
        print(f"最大输出token数: {results['max_output_tokens']}")
        print(f"是否使用长文本: {'是' if results['use_long_context'] else '否'}")
        print(f"总运行时间: {fmt(results['total_time'], 2)} 秒")
        print(f"每秒请求数 (RPS): {fmt(results['requests_per_second'], 2)}")
        print(f"总输出token数: {results['total_output_tokens']}")
        print(f"模型名称: {results['model']}")

        print("\n延迟统计 (单位: 秒):")
        print(f"平均延迟: {fmt(results['latency']['average'])}")
        print(f"延迟 P50: {fmt(results['latency']['p50'])}")
        print(f"延迟 P95: {fmt(results['latency']['p95'])}")
        print(f"延迟 P99: {fmt(results['latency']['p99'])}")

        print("\nToken生成速度 (tokens/sec):")
        print(f"平均速度: {fmt(results['tokens_per_second']['average'], 2)}")
        print(f"速度 P50: {fmt(results['tokens_per_second']['p50'], 2)}")
        print(f"速度 P95: {fmt(results['tokens_per_second']['p95'], 2)}")
        print(f"速度 P99: {fmt(results['tokens_per_second']['p99'], 2)}")

        print("\n首token响应时间 (单位: 秒):")
        print(f"平均时间: {fmt(results['time_to_first_token']['average'])}")
        print(f"TTFT P50: {fmt(results['time_to_first_token']['p50'])}")
        print(f"TTFT P95: {fmt(results['time_to_first_token']['p95'])}")
        print(f"TTFT P99: {fmt(results['time_to_first_token']['p99'])}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark ai model with LLM")
    parser.add_argument("--num_requests", type=int, required=True, help="Number of requests to make")
    parser.add_argument("--concurrency", type=int, required=True, help="Number of concurrent requests")
    parser.add_argument("--request_timeout", type=int, default=60, help="Timeout for each request in seconds (default: 60)")
    parser.add_argument("--output_tokens", type=int, default=50, help="Number of output tokens (default: 50)")
    parser.add_argument("--llm_url", type=str, required=True, help="URL of the LLM server")
    parser.add_argument("--api_key", type=str, required=False, default="default", help="API key for LLM server")
    parser.add_argument("--model", type=str, default="deepseek-r1", 
                       help="Model name to use for inference (default: deepseek-r1)")
    parser.add_argument("--use_long_context", action="store_true", help="Use long context prompt pairs instead of short prompts")
    parser.add_argument("--output_format", type=str, choices=['json', 'line', 'both'], 
                       default='line', help="Output format (json/line/both)")
    args = parser.parse_args()

    results = asyncio.run(run_benchmark(
        args.num_requests, 
        args.concurrency, 
        args.request_timeout, 
        args.output_tokens, 
        args.llm_url, 
        args.api_key,
        args.model,
        args.use_long_context
    ))
    print_results(results, args.output_format)

else:
    # When imported as a module, provide the run_benchmark function
    __all__ = ['run_benchmark']
