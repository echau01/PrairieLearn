// @ts-check
import _ from 'lodash';
import * as express from 'express';
import { z } from 'zod';
import asyncHandler from 'express-async-handler';

import * as error from '@prairielearn/error';

import {
  getAndRenderVariant,
  renderPanelsForSubmission,
  setRendererHeader,
} from '../../lib/question-render.js';
import * as issues from '../../lib/issues.js';
import { logPageView } from '../../middlewares/logPageView.js';
import { setQuestionCopyTargets } from '../../lib/copy-question.js';
import {
  processSubmission,
  validateVariantAgainstQuestion,
} from '../../lib/question-submission.js';
import { IdSchema } from '../../lib/db-types.js';

const router = express.Router();

async function processIssue(req, res) {
  const description = req.body.description;
  if (!_.isString(description) || description.length === 0) {
    throw new error.HttpStatusError(400, 'A description of the issue must be provided');
  }

  const variantId = req.body.__variant_id;
  await validateVariantAgainstQuestion(variantId, res.locals.question.id);
  await issues.insertIssue({
    variantId,
    studentMessage: description,
    instructorMessage: 'instructor-reported issue',
    manuallyReported: true,
    courseCaused: true,
    courseData: _.pick(res.locals, ['variant', 'question', 'course_instance', 'course']),
    systemData: {},
    authnUserId: res.locals.authn_user.user_id,
  });
  return variantId;
}

router.post(
  '/',
  asyncHandler(async (req, res) => {
    if (req.body.__action === 'grade' || req.body.__action === 'save') {
      const variant_id = await processSubmission(req, res);
      res.redirect(
        `${res.locals.urlPrefix}/question/${res.locals.question.id}/preview/?variant_id=${variant_id}`,
      );
    } else if (req.body.__action === 'report_issue') {
      const variant_id = await processIssue(req, res);
      res.redirect(
        `${res.locals.urlPrefix}/question/${res.locals.question.id}/preview/?variant_id=${variant_id}`,
      );
    } else {
      throw new error.HttpStatusError(400, `unknown __action: ${req.body.__action}`);
    }
  }),
);

router.get(
  '/variant/:variant_id(\\d+)/submission/:submission_id(\\d+)',
  asyncHandler(async (req, res) => {
    const { submissionPanel, extraHeadersHtml } = await renderPanelsForSubmission({
      submission_id: req.params.submission_id,
      question_id: res.locals.question.id,
      instance_question_id: null,
      variant_id: req.params.variant_id,
      urlPrefix: res.locals.urlPrefix,
      questionContext: null,
      csrfToken: null,
      authorizedEdit: null,
      renderScorePanels: false,
    });
    res.send({ submissionPanel, extraHeadersHtml });
  }),
);

router.get(
  '/',
  asyncHandler(async (req, res) => {
    const variant_seed = req.query.variant_seed ? z.string().parse(req.query.variant_seed) : null;
    const variant_id = req.query.variant_id ? IdSchema.parse(req.query.variant_id) : null;
    // req.query.variant_id might be undefined, which will generate a new variant
    await getAndRenderVariant(variant_id, variant_seed, res.locals);
    await logPageView('instructorQuestionPreview', req, res);
    await setQuestionCopyTargets(res);

    setRendererHeader(res);
    res.render(import.meta.filename.replace(/\.js$/, '.ejs'), res.locals);
  }),
);

export default router;
