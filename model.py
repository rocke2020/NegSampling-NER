import numpy as np
from typing import List
import torch
from torch import nn
from transformers import BertModel

from misc import flat_list
from misc import iterative_support, conflict_judge
from utils import UnitAlphabet, LabelAlphabet


class PhraseClassifier(nn.Module):

    def __init__(self,
                 lexical_vocab: UnitAlphabet,
                 label_vocab: LabelAlphabet,
                 hidden_dim: int,
                 dropout_rate: float,
                 neg_rate: float,
                 bert_path: str):
        super(PhraseClassifier, self).__init__()

        self._lexical_vocab = lexical_vocab
        self._label_vocab = label_vocab
        self._neg_rate = neg_rate

        self._encoder = BERT(bert_path)
        self._classifier = MLP(self._encoder.dimension * 4, hidden_dim, len(label_vocab), dropout_rate)
        self._criterion = nn.NLLLoss()

    def forward(self, var_h, **kwargs):
        """ output shape: batch_size, word_num, word_num, len(label_vocab) """
        # shape: batch-size, words_num, bert_hid_dim
        con_repr = self._encoder(var_h, kwargs["mask_mat"], kwargs["starts"])

        batch_size, word_num, bert_hid_dim = con_repr.size()
        ext_row = con_repr.unsqueeze(2).expand(batch_size, word_num, word_num, bert_hid_dim)
        ext_column = con_repr.unsqueeze(1).expand_as(ext_row)
        # table shape: batch_size, word_num, word_num, 4 * bert_hid_dim
        table = torch.cat([ext_row, ext_column, ext_row - ext_column, ext_row * ext_column], dim=-1)
        # shape: batch_size, word_num, word_num, len(label_vocab)
        result = self._classifier(table)
        return result

    def _pre_process_input(self, utterances: List[List[str]]):
        """ utterances, batch of sentences: List[List[str]], List[str] is one sentence.
        sentence 1
        words:    word0, word1, word2,
        tokens:   (token0, token1), (token2), (token3, token4)
        position: 0, 2, 3
        start:    1, 3, 4, 7

        sentence 2
        words:    word0, word1, word2, word3
        tokens:   (token0, token1), (token2), (token3, token4), (token5)
        position: 0, 2, 3, 5
        start:    1, 3, 4, 6
        
        max_words_number is the max word numbers of each sentence in batch sentences
        max_tokens_number is the max token numbers of each sentence in batch sentences
        max_tokens_number = 6
        max_words_number = 4
        
        """
        words_num_in_sentences = [len(s) for s in utterances]
        # max_words_number is the max words numbers in batch sentences
        max_words_number = max(words_num_in_sentences)
        batch_sentences = iterative_support(self._lexical_vocab.tokenize, utterances)
        units, positions = [], []

        for tokens_groups in batch_sentences:
            units.append(flat_list(tokens_groups))
            cum_list = np.cumsum([len(tokens_per_word) for tokens_per_word in tokens_groups]).tolist()
            positions.append([0] + cum_list[:-1])

        sizes = [len(u) for u in units]
        # max_tokens_number is the max token numbers of each sentence in batch sentences
        max_tokens_number = max(sizes)
        cls_sign = self._lexical_vocab.CLS_SIGN
        sep_sign = self._lexical_vocab.SEP_SIGN
        pad_sign = self._lexical_vocab.PAD_SIGN
        pad_unit = [[cls_sign] + s + [sep_sign] + [pad_sign] * (max_tokens_number - len(s)) for s in units]
        starts = [[ln + 1 for ln in u] + [max_tokens_number + 1] * (max_words_number - len(u)) for u in positions]

        var_unit = torch.LongTensor([self._lexical_vocab.index(u) for u in pad_unit])
        attn_mask = torch.LongTensor([[1] * (lg + 2) + [0] * (max_tokens_number - lg) for lg in sizes])
        var_start = torch.LongTensor(starts)

        if torch.cuda.is_available():
            var_unit = var_unit.cuda()
            attn_mask = attn_mask.cuda()
            var_start = var_start.cuda()
        return var_unit, attn_mask, var_start, words_num_in_sentences

    def _pre_process_output(self, entities, words_num_in_sentences):
        """  
        sentences: [['Somerset', '83', 'and', '174', '(', 'P.', 'Simmons', '4-38', ')', '.']]
        entities: [[(0, 0, 'ORG'), (5, 6, 'PER')]]
        """        
        positions, labels = [], []
        batch_size = len(entities)

        for utt_i in range(0, batch_size):
            for segment in entities[utt_i]:
                positions.append((utt_i, segment[0], segment[1]))
                labels.append(segment[2])

        for utt_i in range(0, batch_size):
            reject_set = [(e[0], e[1]) for e in entities[utt_i]]
            s_len = words_num_in_sentences[utt_i]
            neg_num = int(s_len * self._neg_rate) + 1

            candies = flat_list([[(i, j) for j in range(i, s_len) if (i, j) not in reject_set] for i in range(s_len)])
            if len(candies) > 0:
                sample_num = min(neg_num, len(candies))
                assert sample_num > 0

                np.random.shuffle(candies)
                for i, j in candies[:sample_num]:
                    positions.append((utt_i, i, j))
                    labels.append("O")

        var_lbl = torch.LongTensor(iterative_support(self._label_vocab.index, labels))
        if torch.cuda.is_available():
            var_lbl = var_lbl.cuda()
        return positions, var_lbl

    def estimate(self, sentences, entities):
        """  
        sentences: [['Somerset', '83', 'and', '174', '(', 'P.', 'Simmons', '4-38', ')', '.']]
        entities: [[(0, 0, 'ORG'), (5, 6, 'PER')]]
        """
        var_sent, attn_mask, start_mat, words_num_in_sentences = self._pre_process_input(sentences)
        # score_t shape: batch_size, word_num, word_num, len(label_vocab)
        score_t = self(var_sent, mask_mat=attn_mask, starts=start_mat)

        positions, targets = self._pre_process_output(entities, words_num_in_sentences)
        # flat_s shape: labels_num, len(label_vocab)
        flat_s = torch.cat([score_t[[i], j, k] for i, j, k in positions], dim=0)
        return self._criterion(torch.log_softmax(flat_s, dim=-1), targets)

    def inference(self, sentences):
        var_sent, attn_mask, starts, words_num_in_sentences = self._pre_process_input(sentences)
        # shape: batch_size, word_num, word_num, len(label_vocab)
        log_items = self(var_sent, mask_mat=attn_mask, starts=starts)

        # score_t = torch.log_softmax(log_items, dim=-1)
        val_table, idx_table = torch.max(log_items, dim=-1)
        # shape: batch_size, word-num, word-num
        listing_it = idx_table.cpu().numpy().tolist()
        listing_vt = val_table.cpu().numpy().tolist()
        # convert idx to label item, such as "O", 'PER', shape not changed.
        label_table = iterative_support(self._label_vocab.get, listing_it)

        candidates = []
        for label_mat, val_mat, sent_len in zip(label_table, listing_vt, words_num_in_sentences):
            candidates.append([])
            for i in range(0, sent_len):
                for j in range(i, sent_len):
                    if label_mat[i][j] != "O":
                        candidates[-1].append((i, j, label_mat[i][j], val_mat[i][j]))

        entities = []
        for segments in candidates:
            ordered_seg = sorted(segments, key=lambda e: e[-1], reverse=True)
            filter_list = []
            for elem in ordered_seg:
                flag = False
                current = (elem[0], elem[1])
                for prior in filter_list:
                    flag = conflict_judge(current, (prior[0], prior[1]))
                    if flag:
                        break
                if not flag:
                    filter_list.append((elem[0], elem[1], elem[2]))
            entities.append(sorted(filter_list, key=lambda e: e[0]))
        return entities


class BERT(nn.Module):

    def __init__(self, source_path):
        super(BERT, self).__init__()
        self._repr_model = BertModel.from_pretrained(source_path)

    @property
    def dimension(self):
        return 768

    def forward(self, var_h, attn_mask, starts):
        outputs = self._repr_model(var_h, attention_mask=attn_mask)
        last_hidden_states = outputs[0]
        batch_size, _, hidden_dim = last_hidden_states.size()
        # starts shape: batch_size, words_num in each sentence
        _, words_num = starts.size()
        positions = starts.unsqueeze(-1).expand(batch_size, words_num, hidden_dim)
        # return shape: batch-size, words_num, hid_dim
        return torch.gather(last_hidden_states, dim=-2, index=positions)


class MLP(nn.Module):

    def __init__(self, input_dim, hidden_dim, output_dim, dropout_rate):
        super(MLP, self).__init__()

        self._activator = nn.Sequential(nn.Linear(input_dim, hidden_dim),
                                        nn.Tanh(),
                                        nn.Linear(hidden_dim, output_dim))
        self._dropout = nn.Dropout(dropout_rate)

    def forward(self, var_h):
        return self._activator(self._dropout(var_h))
