"""Exact expectation checks, not comparisons against the implementation text.

Ported from the frozen tests/test_estimators.py (one case dropped: it tested the
research module methods.physical_table_audit, kept at the tag agent-pre-slim-2026-10-22).
"""
import itertools
import unittest
import torch
from agent.optim.estimators import (
    score_updates, rloo_advantages, ht_peer_baseline,
    fit_control_coefficients, nested_control_variate, aipw_updates,
    allocation_probabilities, lax_continuous_update,
)

class EstimatorTests(unittest.TestCase):
    def test_score_matches_derivative_of_exact_expected_reward(self):
        logits=torch.tensor([.3,-.7,1.1],dtype=torch.float64,requires_grad=True)
        rewards=torch.tensor([-1.,3.,.2],dtype=torch.float64)
        p=logits.softmax(0); exact=torch.autograd.grad((p*rewards).sum(),logits)[0]
        scores=torch.eye(3,dtype=torch.float64)-p.detach()[None,:]
        estimate=(p.detach()[:,None]*score_updates(rewards,scores,baseline=1.7)).sum(0)
        torch.testing.assert_close(estimate,exact)

    def test_rloo_joint_expectation_and_exact_ties(self):
        probabilities=torch.tensor([.2,.8],dtype=torch.float64)
        scores=torch.eye(2,dtype=torch.float64)-probabilities[None,:]
        reward=torch.tensor([1.,-2.],dtype=torch.float64); result=torch.zeros(2,dtype=torch.float64)
        for a,b in itertools.product(range(2),repeat=2):
            advantage=rloo_advantages(reward[[a,b]])
            result+=probabilities[a]*probabilities[b]*(advantage[:,None]*scores[[a,b]]).mean(0)
        exact=(probabilities[:,None]*reward[:,None]*scores).sum(0)
        torch.testing.assert_close(result,exact)
        self.assertEqual(rloo_advantages([1e12,1e12]).abs().sum().item(),0)

    def test_aipw_exact_over_all_selection_masks_with_biased_auxiliary(self):
        target=torch.tensor([[1.,-3.],[4.,2.],[-2.,1.]],dtype=torch.float64)
        auxiliary=torch.tensor([[70.,9.],[-20.,5.],[6.,-2.]],dtype=torch.float64)
        probabilities=torch.tensor([.1,.7,.3],dtype=torch.float64)
        expected=torch.zeros_like(target)
        for bits in itertools.product([False,True],repeat=3):
            selected=torch.tensor(bits); weight=torch.where(selected,probabilities,1-probabilities).prod()
            observed=target.clone();observed[~selected]=float('nan')
            expected+=weight*aipw_updates(observed,auxiliary,selected,probabilities)
        torch.testing.assert_close(expected,target)

    def test_ht_peer_baseline_no_self_or_missing_outcome_leakage(self):
        rewards=torch.tensor([2.,5.,-1.],dtype=torch.float64);q=torch.tensor([.1,.3,.8],dtype=torch.float64)
        expected=torch.zeros(3,dtype=torch.float64)
        for bits in itertools.product([False,True],repeat=3):
            selected=torch.tensor(bits);weight=torch.where(selected,q,1-q).prod()
            observed=rewards.clone();observed[~selected]=float('nan')
            expected+=weight*ht_peer_baseline(observed,selected,q)
        torch.testing.assert_close(expected,(rewards.sum()-rewards)/2)

    def test_nested_exact_uniform_subsets(self):
        target=torch.tensor([[1.,2.],[3.,-1.],[0.,8.],[4.,0.]],dtype=torch.float64)
        proxy=torch.stack([target*3+1,target.flip(0)],dim=1)
        beta=torch.tensor([.2,-.5],dtype=torch.float64)
        estimates=[nested_control_variate(target[list(ix)],proxy,list(ix),beta) for ix in itertools.combinations(range(4),2)]
        torch.testing.assert_close(torch.stack(estimates).mean(0),target.mean(0))

    def test_covariance_fit_subtracts_gradient_means(self):
        x=torch.tensor([[1.,5.],[2.,4.],[3.,3.],[4.,2.]],dtype=torch.float64)
        components=torch.stack([x,-2*x+17],dim=1)
        fitted=fit_control_coefficients(x,components,provenance='disjoint-calibration',ridge=1e-8)
        predicted=torch.einsum('k,nkp->np',fitted.coefficients,components)
        torch.testing.assert_close(predicted-predicted.mean(0),x-x.mean(0),atol=1e-7,rtol=1e-7)

    def test_acquisition_count_floor_and_zero_discrepancy(self):
        q=allocation_probabilities([0.,1.,4.,25.],[1.,2.,2.,3.],expected_hf_cost_budget=1.5,q_min=.05)
        self.assertAlmostEqual((q*torch.tensor([1.,2.,2.,3.])).sum().item(),1.5,places=10)
        self.assertGreaterEqual(q.min().item(),.05)
        torch.testing.assert_close(allocation_probabilities([0.,0.],1.,expected_hf_count=1.),torch.tensor([.5,.5],dtype=torch.float64))

    def test_zero_discrepancy_saturation_and_variable_cost_budget(self):
        q=allocation_probabilities([1.,0.],[1.,9.],expected_hf_cost_budget=5.,q_min=.1)
        torch.testing.assert_close(q,torch.tensor([1.,.1],dtype=torch.float64))
        with self.assertRaises(ValueError):
            allocation_probabilities([1.,2.],[1.,2.],expected_hf_count=1.)

    def test_purchased_failures_require_finite_target(self):
        with self.assertRaises(ValueError):
            aipw_updates([[float('nan')]],[[0.]], [True],[.5])
        with self.assertRaises(ValueError):
            aipw_updates([[1.]],[[0.]], [True],[0.])
        result=aipw_updates([[-10.]],[[0.]], [True],[.5])
        self.assertEqual(result.item(),-20.)

    def test_lax_matches_analytic_normal_quadratic_gradient(self):
        # Exact normal moments for H=x²+3x, L=x², x~N(mu,sigma²).
        mu=.7; sigma=.4; c=.8
        # Two-point symmetric quadrature exactly integrates this cubic score moment.
        z=torch.tensor([-3**.5,0.,3**.5],dtype=torch.float64)
        w=torch.tensor([1/6,2/3,1/6],dtype=torch.float64)
        x=mu+sigma*z;score=(x-mu)/sigma**2
        sf=((x*x+3*x-c*x*x)*score*w).sum()
        path=2*mu
        self.assertAlmostEqual((c*path+sf).item(),2*mu+3,places=12)

    def test_lax_continuous_update_is_the_registered_formula(self):
        path=torch.tensor([[1.,2.],[3.,-1.],[0.,4.]],dtype=torch.float64)
        scores=torch.tensor([[.5,-.5],[1.,2.]],dtype=torch.float64)
        hf=torch.tensor([2.,-1.],dtype=torch.float64);lf=torch.tensor([1.5,-.5],dtype=torch.float64)
        expected=.7*path.mean(0)+((hf-.7*lf)[:,None]*scores).mean(0)
        torch.testing.assert_close(lax_continuous_update(path,scores,hf,lf,coefficient=.7),expected)
        with self.assertRaises(ValueError):
            lax_continuous_update(path,scores[:,:1],hf,lf)

    def test_rloo_requires_two_finite_rewards(self):
        torch.testing.assert_close(rloo_advantages([1.,3.]),torch.tensor([-2.,2.],dtype=torch.float64))
        for bad in ([1.],[1.,float("nan")]):
            with self.assertRaises(ValueError):rloo_advantages(bad)

if __name__=='__main__':unittest.main()
