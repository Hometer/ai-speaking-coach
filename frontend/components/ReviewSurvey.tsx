"use client";
import { useEffect, useRef, useState } from "react";
import { recordProductEvent, reviewSurvey, type SurveyAnswer } from "@/lib/api";

type Saved={sessionId:string;answer:SurveyAnswer | null;version:number};
export default function ReviewSurvey({sessionId}:{sessionId:string}) {
  const [saved,setSaved]=useState<Saved | null>(null);
  const [pending,setPending]=useState<string | null>(null);
  const [error,setError]=useState<{sessionId:string;message:string} | null>(null);
  const generation=useRef(0);
  const answer=saved?.sessionId===sessionId?saved.answer:null;
  useEffect(()=>{
    const current=++generation.current;
    recordProductEvent(sessionId,"review_view",sessionId);
    void reviewSurvey(sessionId,"shown").then(result=>{
      if(current===generation.current)setSaved(previous=>previous?.sessionId===sessionId && previous.version>result.version?previous:{sessionId,...result});
    }).catch(()=>{if(current===generation.current)setError({sessionId,message:"问卷暂时不可用，你仍可查看回顾。"});});
    return ()=>{generation.current+=1;};
  },[sessionId]);
  async function choose(value:SurveyAnswer) {
    const current=generation.current;
    setPending(sessionId);setError(null);
    try {
      const result=await reviewSurvey(sessionId,value);
      if(current===generation.current)setSaved(previous=>previous?.sessionId===sessionId && previous.version>result.version?previous:{sessionId,...result});
    } catch { if(current===generation.current)setError({sessionId,message:"本次评价未保存，请稍后重试。"}); }
    finally { if(current===generation.current)setPending(null); }
  }
  return <section className="panel panel-padding mt-5" aria-label="回顾反馈问卷">
    <h3>这次反馈对你有帮助吗？</h3><p className="text-xs muted mt-2">可自愿评价，也可以暂时不回答。不会影响学习结果。</p>
    <div className="flex flex-wrap gap-2 mt-3">{([["helpful","有帮助"],["not_helpful","帮助不大"],["uncertain","暂时无法判断"]] as const).map(([value,label])=><button key={value} disabled={pending===sessionId} aria-pressed={answer===value} onClick={()=>void choose(value)} className={`btn btn-small ${answer===value?"btn-primary":"btn-secondary"}`}>{label}</button>)}</div>
    {answer && <p className="text-xs muted mt-2" role="status">已记录，可以修改。</p>}{error?.sessionId===sessionId && <p className="text-xs mt-2" role="alert">{error.message}</p>}
  </section>;
}
