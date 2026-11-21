"""Joint discrete/continuous policy losses (ported from the frozen tests/test_joint_losses.py)."""
import itertools
import unittest
from types import SimpleNamespace
import torch
from agent.policies.losses import joint_policy_loss

class JointLossTests(unittest.TestCase):
    def make_sample(self):
        # Distinct symbolic derivatives represent score_s, score_x and dx/dtheta.
        theta=torch.zeros(3,dtype=torch.float64,requires_grad=True)
        ds=torch.tensor([[.3,-.1,.2],[-.2,.4,-.1]],dtype=torch.float64)
        dx=torch.tensor([[.7,.2,-.3],[-.5,.1,.6]],dtype=torch.float64)
        jac=torch.tensor([[[.2,.1,.0]],[[.1,-.2,.3]]],dtype=torch.float64)
        sample=SimpleNamespace(actions=torch.tensor([0,1]),
            values=torch.tensor([[.5],[.7]],dtype=torch.float64)+torch.einsum("nkp,p->nk",jac,theta),
            discrete_log_prob=ds@theta,continuous_log_prob=dx@theta)
        sample.log_prob=sample.discrete_log_prob+sample.continuous_log_prob
        return sample,theta,ds,dx,jac

    def test_sparse_aipw_backprop_matches_exact_purchase_expectation(self):
        hf=torch.tensor([.8,-.2],dtype=torch.float64)
        lf=torch.tensor([.3,.1],dtype=torch.float64);q=torch.tensor([.25,.7],dtype=torch.float64)
        expected=torch.zeros(3,dtype=torch.float64)
        for draw in itertools.product((False,True),repeat=2):
            sample,theta,ds,dx,jac=self.make_sample();mask=torch.tensor(draw)
            observed=hf.clone();observed[~mask]=float("nan")
            loss,_=joint_policy_loss(sample,method="scalar-aipw",hf_rewards=observed,
                lf_rewards=lf,selected=mask,probabilities=q,coefficient=.6)
            probability=torch.where(mask,q,1-q).prod()
            expected+=probability*torch.autograd.grad(loss,theta)[0]
        sample,theta,ds,dx,jac=self.make_sample()
        loss,_=joint_policy_loss(sample,method="hf-rloo",hf_rewards=hf)
        torch.testing.assert_close(expected,torch.autograd.grad(loss,theta)[0])

    def test_lax_backprop_preserves_discrete_and_continuous_terms(self):
        sample,theta,ds,dx,jac=self.make_sample()
        hf=torch.tensor([.8,-.2],dtype=torch.float64);lf=torch.tensor([.3,.1],dtype=torch.float64)
        q=torch.tensor([.25,.7],dtype=torch.float64);mask=torch.tensor([True,False])
        observed=torch.tensor([.8,float("nan")],dtype=torch.float64)
        grad=torch.tensor([[.4],[-.2]],dtype=torch.float64)
        loss,_=joint_policy_loss(sample,method="continuous-lax",hf_rewards=observed,
            lf_rewards=lf,selected=mask,probabilities=q,coefficient=.6,proxy_gradients=grad)
        discrepancy=torch.tensor([(.8-.6*.3)/.25,0.],dtype=torch.float64)
        signal=.6*lf+discrepancy
        expected=-(((signal-signal.flip(0))[:,None]*ds)+
            ((discrepancy-discrepancy.flip(0))[:,None]*dx)+.6*(grad[:,:,None]*jac).sum(1)).mean(0)
        torch.testing.assert_close(torch.autograd.grad(loss,theta)[0],expected)

    def test_common_term_is_applied_once_and_keeps_categorical_score(self):
        sample,theta,ds,dx,jac=self.make_sample()
        hf=torch.tensor([.8,-.2],dtype=torch.float64);known=torch.tensor([.05,.07],dtype=torch.float64)
        gradient=torch.tensor([[.1],[.2]],dtype=torch.float64)
        loss,_=joint_policy_loss(sample,method="hf-rloo",hf_rewards=hf,
            known_rewards=known,known_gradients=gradient)
        baseline=hf.flip(0)
        expected=-((hf-baseline)[:,None]*ds).mean(0)
        expected-=((hf-known-(baseline-known.flip(0)))[:,None]*dx).mean(0)
        expected-=(gradient[:,:,None]*jac).sum(1).mean(0)
        torch.testing.assert_close(torch.autograd.grad(loss,theta)[0],expected)

    def test_lax_common_term_does_not_double_count_path(self):
        sample,theta,ds,dx,jac=self.make_sample()
        reward=torch.tensor([.8,-.2],dtype=torch.float64);known=torch.tensor([.05,.07],dtype=torch.float64)
        grad=torch.tensor([[.4],[-.2]],dtype=torch.float64);kg=torch.tensor([[.1],[.2]],dtype=torch.float64)
        loss,_=joint_policy_loss(sample,method="continuous-lax",hf_rewards=reward,lf_rewards=reward,
            selected=[True,True],probabilities=[1.,1.],proxy_gradients=grad,
            known_rewards=known,known_gradients=kg)
        expected=-((reward-reward.flip(0))[:,None]*ds).mean(0)
        expected-=(grad[:,:,None]*jac).sum(1).mean(0)
        torch.testing.assert_close(torch.autograd.grad(loss,theta)[0],expected)

    def test_known_only_has_identical_updates_for_sparse_q_and_nonunit_beta(self):
        known=torch.tensor([.05,.07],dtype=torch.float64)
        kg=torch.tensor([[.1],[.2]],dtype=torch.float64)
        expected=None
        for method in ("hf-rloo","lf-rloo","scalar-aipw","continuous-lax","hf-path"):
            for draw in itertools.product((False,True),repeat=2):
                sample,theta,ds,dx,jac=self.make_sample()
                mask=torch.tensor(draw)
                observed=known.clone()
                if method in ("scalar-aipw","continuous-lax"):observed[~mask]=float("nan")
                loss,_=joint_policy_loss(sample,method=method,hf_rewards=observed,lf_rewards=known,
                    selected=mask,probabilities=[.2,.7],coefficient=.35,
                    proxy_gradients=kg,hf_gradients=kg,known_rewards=known,known_gradients=kg)
                actual=torch.autograd.grad(loss,theta)[0]
                if expected is None:expected=actual
                else:torch.testing.assert_close(actual,expected)

    def test_matching_proxy_with_full_purchase_recovers_conditional_path(self):
        sample,theta,ds,dx,jac=self.make_sample()
        reward=torch.tensor([.4,-.1],dtype=torch.float64)
        gradient=torch.tensor([[.3],[-.4]],dtype=torch.float64)
        loss,_=joint_policy_loss(sample,method="continuous-lax",hf_rewards=reward,
            lf_rewards=reward,selected=[True,True],probabilities=[1.,1.],
            proxy_gradients=gradient)
        expected=-((reward-reward.flip(0))[:,None]*ds).mean(0)
        expected-=(gradient[:,:,None]*jac).sum(1).mean(0)
        torch.testing.assert_close(torch.autograd.grad(loss,theta)[0],expected)

    def test_lax_purchase_expectation_matches_full_purchase_lax(self):
        q=torch.tensor([.25,.7],dtype=torch.float64);hf=torch.tensor([.8,-.2],dtype=torch.float64)
        lf=torch.tensor([.3,.1],dtype=torch.float64);gradient=torch.tensor([[.4],[-.2]],dtype=torch.float64)
        average=torch.zeros(3,dtype=torch.float64)
        for draw in itertools.product((False,True),repeat=2):
            sample,theta,*_=self.make_sample();mask=torch.tensor(draw)
            observed=hf.clone();observed[~mask]=float("nan")
            loss,_=joint_policy_loss(sample,method="continuous-lax",hf_rewards=observed,
                lf_rewards=lf,selected=mask,probabilities=q,coefficient=.6,proxy_gradients=gradient)
            average+=torch.where(mask,q,1-q).prod()*torch.autograd.grad(loss,theta)[0]
        sample,theta,*_=self.make_sample()
        loss,_=joint_policy_loss(sample,method="continuous-lax",hf_rewards=hf,lf_rewards=lf,
            selected=[True,True],probabilities=[1.,1.],coefficient=.6,proxy_gradients=gradient)
        torch.testing.assert_close(average,torch.autograd.grad(loss,theta)[0])

    def test_missing_gradient_does_not_rewrite_valid_forward_reward(self):
        sample,theta,*_=self.make_sample();reward=torch.tensor([.1,.2])
        with self.assertRaises(ValueError):
            joint_policy_loss(sample,method="continuous-lax",hf_rewards=reward,lf_rewards=reward,
                              selected=[True,True],probabilities=[1.,1.],proxy_gradients=None)
        torch.testing.assert_close(reward,torch.tensor([.1,.2]))

    def test_purchased_failure_is_retained_and_zero_purchase_is_defined(self):
        for mask,hf in [([True,False],[-1e6,float("nan")]),
                        ([False,False],[float("nan"),float("nan")])]:
            sample,theta,*_=self.make_sample()
            loss,info=joint_policy_loss(sample,method="scalar-aipw",hf_rewards=hf,
                lf_rewards=[.1,.2],selected=mask,probabilities=[.2,.2])
            self.assertTrue(torch.isfinite(torch.autograd.grad(loss,theta)[0]).all())
            self.assertEqual(info["HF_purchased"],sum(mask))

if __name__=="__main__": unittest.main()
