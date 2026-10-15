"use client";
import { useEffect, useState } from "react";
import { recordProductEvent, reviewSurvey, type SurveyAnswer } from "@/lib/api";

export default function ReviewSurvey({sessionId}:{sessionId:string}) {
  const [answer,setAnswer]=useState<SurveyAnswer | null>(null);
  const [pending,setPending]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>{
    let active=true;
    recordProductEvent(sessionId,"review_view",sessionId);
    void reviewSurvey(sessionId,"shown").then(result=>{if(active)setAnswer(result.answer);}).catch(()=>{if(active)setError("问卷暂时不可用，你仍可查看回顾。");});
    return ()=>{active=false;};
  },[sessionId]);
  async function choose(value:SurveyAnswer) {
    setPending(true);setError("");
    try{setAnswer((await reviewSurvey(sessionId,value)).answer);}
    catch{setError("本次评价未保存，请稍后重试。");}
    finally{setPending(false);}
  }
  return <section className="panel panel-padding mt-5" aria-label="回顾反馈问卷">
    <h3>这次反馈对你有帮助吗？</h3><p className="text-xs muted mt-2">可自愿评价，也可以暂时不回答。不会影响学习结果。</p>
    <div className="flex flex-wrap gap-2 mt-3">{([["helpful","有帮助"],["not_helpful","帮助不大"],["uncertain","暂时无法判断"]] as const).map(([value,label])=><button key={value} disabled={pending} aria-pressed={answer===value} onClick={()=>void choose(value)} className={`btn btn-small ${answer===value?"btn-primary":"btn-secondary"}`}>{label}</button>)}</div>
    {answer && <p className="text-xs muted mt-2" role="status">已记录，可以修改。</p>}{error && <p className="text-xs mt-2" role="alert">{error}</p>}
  </section>;
}
