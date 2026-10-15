import {cleanup,render,screen} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import LearningMetrics from "./LearningMetrics";
import type {LearningMetrics as Metrics} from "@/lib/types";
afterEach(cleanup);
it("shows unknown samples and numerator/denominator without claiming success",()=>{
  const data={window:{start:"2026-10-01",end:"2026-10-15",scope:"fixture"},counts:{recovery_unresolved:1},metrics:{recovery:{label:"处理中恢复成功率",numerator:0,denominator:0,rate:null,unknown:1,basis:"结果待核对不算成功"}}} as unknown as Metrics;
  render(<LearningMetrics data={data}/>);
  expect(screen.getByText("暂无可计算样本")).toBeInTheDocument();
  expect(screen.getByText(/分子 0 \/ 分母 0；未知或待观察 1/)).toBeInTheDocument();
  expect(screen.queryByText("达标")).not.toBeInTheDocument();
});
